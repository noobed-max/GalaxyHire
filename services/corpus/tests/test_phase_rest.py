"""Tests for shared-infrastructure behavior: throttle, metrics, profile-import, scheduler.

The adapter section of this file went with the GalaxyJobsAi sources; pipeline behavior is
covered by test_external_observations.py (the run_observations path is the only write path)."""

from __future__ import annotations

from uuid import uuid4

import pytest

# --- throttle ---------------------------------------------------------------


def test_token_bucket_try_acquire_drains():
    from galaxy.fetch.limits import TokenBucket

    b = TokenBucket(rate=0.0001, capacity=3)  # ~no refill during the test
    assert b.try_acquire() and b.try_acquire() and b.try_acquire()
    assert not b.try_acquire()  # drained


@pytest.mark.asyncio
async def test_throttle_dependency_raises_429_when_drained():
    from fastapi import HTTPException

    from galaxy.api import throttle as th

    th._buckets.clear()
    # first call establishes the bucket and succeeds
    await th.throttle(x_api_key="k1")
    # drain it, then the next call must 429
    th._buckets["k1"]._tokens = 0
    with pytest.raises(HTTPException) as exc:
        await th.throttle(x_api_key="k1")
    assert exc.value.status_code == 429


# --- metrics ----------------------------------------------------------------


def test_metrics_records_and_computes_block_rate():
    from galaxy.common import metrics

    metrics.reset()
    metrics.record_run("greenhouse", ok=True, jobs=10)
    metrics.record_run("greenhouse", ok=True, jobs=5)
    metrics.record_run("greenhouse", ok=False, error="boom")
    metrics.record_circuit_skip("linkedin")
    snap = metrics.snapshot()
    gh = snap["sources"]["greenhouse"]
    assert gh["jobs"] == 15
    assert gh["ok"] == 2 and gh["failed"] == 1
    assert gh["block_rate"] == round(1 / 3, 3)
    assert snap["total_jobs"] == 15
    assert snap["sources"]["linkedin"]["circuit_skips"] == 1


# --- profile import (LLM parse) ---------------------------------------------


class _FakeLLM:
    def __init__(self, args_json: str):
        self._args = args_json

    async def chat(self, messages, **kwargs):
        return {
            "choices": [
                {"message": {"tool_calls": [{"function": {"arguments": self._args}}]}}
            ]
        }


@pytest.mark.asyncio
async def test_profile_import_maps_llm_extraction():
    from galaxy.search.profile_import import import_from_text

    args = (
        '{"name":"Ada Lovelace","email":"ada@x.io","links":["https://github.com/ada"],'
        '"skills":["Python","SQL"],'
        '"projects":[{"title":"Analytical Engine","bullets":["Designed the first algorithm."],'
        '"skills":["Math"]}]}'
    )
    uid = uuid4()
    profile = await import_from_text(uid, "resume text…", client=_FakeLLM(args))
    assert profile.user_id == uid
    assert profile.identity.name == "Ada Lovelace"
    assert {s.name for s in profile.skills} == {"Python", "SQL"}
    assert profile.projects[0].title == "Analytical Engine"
    assert profile.projects[0].verbatim is True  # pasted content used as-is


def test_profile_import_lenient_json():
    from galaxy.search.profile_import import parse_tool_json

    assert parse_tool_json('{"name":"x"}')["name"] == "x"
    assert parse_tool_json('sure: {"name":"y"} done')["name"] == "y"
    assert parse_tool_json("not json") == {}


# --- scheduler config -------------------------------------------------------


def test_scheduler_worker_settings_valid():
    from galaxy.worker.scheduler import WorkerSettings

    # Maintenance only: collection is app-triggered, the scheduler never scrapes (a scheduled
    # sweep would hammer job boards unattended).
    assert len(WorkerSettings.cron_jobs) == 2
    names = {f.__name__ for f in WorkerSettings.functions}
    assert names == {"cron_embed", "cron_dedup_global"}
