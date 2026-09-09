from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from pydantic import BaseModel


class LlmPayload(BaseModel):
    value: str = ""
    items: list[str] = []


class FakeSettings:
    def __init__(self, values: dict[str, str]):
        self.values = values

    def get_setting(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)


class FakeRepo:
    def __init__(self, settings: dict[str, str] | None = None):
        self.settings = FakeSettings(settings or {})


def test_llm_fallback_and_step_resolution_are_safe(monkeypatch):
    from llm import client

    original = client.get_repository()
    try:
        client.configure_repository(FakeRepo({
            "llm_provider": "openai",
            "evaluator_provider": "custom",
            "evaluator_api_key": "test-key",
            "evaluator_model": "local-model",
            "custom_base_url": "http://127.0.0.1:11434/v1",
        }))

        fallback = client.call_llm("system", "user", LlmPayload)
        assert isinstance(fallback, LlmPayload)
        assert fallback.value == ""

        assert client.resolve_config("evaluator") == ("custom", "test-key", "local-model")
        monkeypatch.setattr(client, "_call_custom_raw", lambda *a, **kw: "{}")
        invalid_custom = client.call_llm("system", "user", LlmPayload, step="evaluator")
        assert isinstance(invalid_custom, LlmPayload)
    finally:
        client.configure_repository(original)


def test_core_config_parses_targets_and_safe_ints():
    from core.config import int_cfg, job_targets, profile_for_discovery, terms_for_discovery

    cfg = {"target_role": "Backend Engineer", "bad_number": "not-int"}
    profile = profile_for_discovery({"s": "Python APIs", "skills": [{"n": "FastAPI"}]}, cfg)

    assert profile["desired_position"] == "Backend Engineer"
    assert terms_for_discovery(profile, limit=2)[0].startswith("Backend Engineer")
    assert int_cfg(cfg, "bad_number", default=7, min_value=1, max_value=10) == 7
    assert all("upwork" not in target.lower() for target in job_targets("hn:hiring\nhttps://upwork.com/jobs"))


def test_graph_helpers_handle_empty_and_bad_vector_stores():
    from graph_service.helpers import embedding_space, project_vector, safe_graph_step, vector_table_names

    errors: list[str] = []
    assert safe_graph_step(lambda: (_ for _ in ()).throw(RuntimeError("locked")), "counts", errors)["status"] == "error"
    assert errors == ["counts: locked"]

    assert vector_table_names(SimpleNamespace(list_tables=lambda: {"tables": ["skills", "projects"]})) == ["skills", "projects"]
    x, y, z = project_vector([1, "bad", 2])
    assert any(abs(value) > 0 for value in (x, y, z))

    class BadVec:
        def list_tables(self):
            raise RuntimeError("vector offline")

    repo = SimpleNamespace(vector=SimpleNamespace(vec=BadVec()))
    space = embedding_space(repo)
    assert space["available"] is False
    assert "vector offline" in space["error"]


def test_scheduler_ghost_tick_cancels_when_profile_has_no_targets(monkeypatch):
    import api.scheduler as scheduler

    broadcasts: list[dict] = []

    class Manager:
        async def broadcast(self, payload: dict):
            broadcasts.append(payload)

    @dataclass
    class Job:
        job_id: str

    class JobStore:
        def __init__(self):
            self.updates: list[dict] = []

        def create(self, _kind, _payload):
            return Job("ghost-1")

        def update(self, job_id, **payload):
            self.updates.append({"job_id": job_id, **payload})

    job_store = JobStore()
    repo = SimpleNamespace(
        settings=SimpleNamespace(get_settings=lambda: {}, get_setting=lambda key, default="": "true" if key == "ghost_mode" else default),
        profile=SimpleNamespace(get_profile=lambda: {}),
    )
    monkeypatch.setattr(scheduler, "get_repository", lambda: repo)
    monkeypatch.setattr(scheduler, "get_job_runner", lambda: job_store)
    # The scheduler no longer builds an automation (auto-apply) service, and discovery is corpus
    # retrieval rather than scraping.
    monkeypatch.setattr(scheduler, "get_corpus_discovery_service", lambda: object())
    monkeypatch.setattr(scheduler, "get_generation_service", lambda: object())
    monkeypatch.setattr(scheduler, "get_ranking_service", lambda: object())

    asyncio.run(scheduler.create_ghost_tick(Manager())())

    assert broadcasts[0]["event"] == "ghost_warn"
    assert job_store.updates[-1]["status"] == "cancelled"


def _csv_bytes(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()



