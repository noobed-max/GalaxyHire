"""DB-backed Phase 6 tests: applications tracker, saved searches, blacklist, insights.

Skipped unless GALAXY_TEST_DB=1 with a live Postgres.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker, migrate
from galaxy.dedup.engine import DedupEngine
from galaxy.ingestion.normalize import apply_derived_fields
from galaxy.ingestion.store import PostgresJobStore
from galaxy.models.enums import Site
from galaxy.models.job import Location, RawJobFields, SourceObservation
from galaxy.models.profile import Identity, Preferences, Profile, Skill
from galaxy.search import pipeline_store as ps
from galaxy.search import service
from galaxy.search.index import embed_pending
from galaxy.search.profiles import ProfileStore

pytestmark = pytest.mark.skipif(
    os.getenv("GALAXY_TEST_DB") != "1", reason="set GALAXY_TEST_DB=1 with a live Postgres"
)

NOW = datetime(2026, 7, 23, tzinfo=UTC)


async def _seed_jobs() -> list[str]:
    await migrate()
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        await s.execute(text("DELETE FROM applications"))
        await s.execute(text("DELETE FROM saved_searches"))
        await s.execute(text("DELETE FROM source_observations"))
        await s.execute(text("DELETE FROM canonical_jobs"))
    obs = []
    for i, (title, company) in enumerate(
        [("Python Engineer", "Acme"), ("Python Engineer", "EvilCorp"), ("Data Engineer", "Acme")]
    ):
        f = RawJobFields(
            title=title, company=company, location=Location(remote=True, country="USA"),
            description_md=f"{title} role using Python, Airflow, and Kubernetes. {i}",
        )
        apply_derived_fields(f)
        obs.append(SourceObservation(site=Site.GREENHOUSE, source_job_id=f"j{i}",
                                     url=f"https://x/j{i}", observed_at=NOW, raw_title=title, fields=f))
    jobs = DedupEngine().dedup(obs).jobs
    await PostgresJobStore().upsert_jobs(jobs)
    await embed_pending()
    return [j.canonical_job_id for j in jobs]


async def _profile(uid, blacklist=None):
    p = Profile(
        user_id=uid,
        identity=Identity(name="Ada", email="ada@x.io"),
        skills=[Skill(name="Python", tags=["swe"])],
        preferences=Preferences(blacklist=blacklist or []),
    )
    await ProfileStore().upsert(p)


@pytest.mark.asyncio
async def test_applications_tracker_and_stats():
    uid = uuid4()
    ids = await _seed_jobs()
    await _profile(uid)
    # tailor creates an application row
    from galaxy.generation import service as tailor
    await tailor.tailor(uid, ids[0], check_live=False)

    apps = await ps.list_applications(uid)
    assert len(apps) == 1
    app_id = apps[0]["id"]
    assert apps[0]["title"] == "Python Engineer"

    ok = await ps.set_status(uid, app_id, "Applied", None)
    assert ok
    assert not await ps.set_status(uid, app_id, "Bogus", None)  # non-canonical rejected

    stats = await ps.pipeline_stats(uid)
    assert stats["total"] == 1
    assert stats["by_status"].get("Applied") == 1


@pytest.mark.asyncio
async def test_persistent_blacklist_excludes_company():
    uid = uuid4()
    await _seed_jobs()
    await _profile(uid, blacklist=["EvilCorp"])
    ranked = await service.search(user_id=uid, search_term="python engineer", limit=50)
    companies = {j.company for j in ranked}
    assert "EvilCorp" not in companies  # blacklisted
    assert "Acme" in companies


@pytest.mark.asyncio
async def test_saved_search_roundtrip_and_new_since():
    uid = uuid4()
    await _seed_jobs()
    await _profile(uid)
    saved = await ps.save_search(uid, "python remote", {"search_term": "python", "remote": True})
    assert saved["name"] == "python remote"
    lst = await ps.list_searches(uid)
    assert len(lst) == 1
    # "new since a future timestamp" → nothing (all jobs are older)
    future = datetime(2027, 1, 1, tzinfo=UTC).isoformat()
    none_new = await service.search(user_id=uid, search_term="python", seen_after=future, limit=50)
    assert none_new == []


@pytest.mark.asyncio
async def test_skill_gap_from_applications():
    uid = uuid4()
    ids = await _seed_jobs()
    await _profile(uid)  # has Python only
    from galaxy.generation import service as tailor
    await tailor.tailor(uid, ids[0], check_live=False)  # a Python/Airflow/Kubernetes job

    gaps = await ps.skill_gap(uid)
    skills = {g["skill"] for g in gaps}
    assert "python" not in skills  # already have it
    assert "kubernetes" in skills or "airflow" in skills  # missing → suggested
