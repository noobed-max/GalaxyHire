"""DB-backed tailor round-trip (docs/05, docs/07 §3). Skipped unless GALAXY_TEST_DB=1."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker, migrate
from galaxy.dedup.engine import DedupEngine
from galaxy.generation import service as tailor_service
from galaxy.generation.assets import load_asset
from galaxy.ingestion.normalize import apply_derived_fields
from galaxy.ingestion.store import PostgresJobStore
from galaxy.models.enums import Site
from galaxy.models.job import Location, RawJobFields, SourceObservation
from galaxy.models.profile import Identity, Profile, Project, Skill
from galaxy.search.profiles import ProfileStore

pytestmark = pytest.mark.skipif(
    os.getenv("GALAXY_TEST_DB") != "1", reason="set GALAXY_TEST_DB=1 with a live Postgres"
)

NOW = datetime(2026, 7, 23, tzinfo=UTC)


async def _seed_job() -> str:
    await migrate()
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        await s.execute(text("DELETE FROM applications"))
        await s.execute(text("DELETE FROM source_observations"))
        await s.execute(text("DELETE FROM canonical_jobs"))
    f = RawJobFields(
        title="Senior Python Engineer", company="Acme",
        location=Location(remote=True, country="USA"),
        description_md="Build Django APIs on Postgres and AWS. Python required.",
    )
    apply_derived_fields(f)
    obs = SourceObservation(site=Site.GREENHOUSE, source_job_id="j-1", url="https://x/j-1",
                            observed_at=NOW, raw_title=f.title, fields=f)
    jobs = DedupEngine().dedup([obs]).jobs
    await PostgresJobStore().upsert_jobs(jobs)
    return jobs[0].canonical_job_id


async def _seed_profile(uid):
    profile = Profile(
        user_id=uid,
        identity=Identity(name="Ada Dev", email="ada@x.io"),
        roles=["swe"],
        skills=[Skill(name="Python", tags=["swe"]), Skill(name="Django", tags=["swe"])],
        projects=[
            Project(title="Django API", bullets=["Built a Django API on Postgres."],
                    skills=["Python", "Django", "Postgres"], role_tags=["swe"]),
        ],
    )
    await ProfileStore().upsert(profile)


@pytest.mark.asyncio
async def test_tailor_selects_scores_and_renders():
    uid = uuid4()
    job_id = await _seed_job()
    await _seed_profile(uid)

    result = await tailor_service.tailor(uid, job_id, check_live=False)
    assert result["selection"]["projects"], "should select the relevant project"
    assert result["selection"]["projects"][0]["title"] == "Django API"
    assert 0.0 < result["ats_score"]["overall"] <= 1.0
    assert "lint" in result

    # an application row was persisted as Tailored
    sm = get_sessionmaker()
    async with sm() as s:
        row = (await s.execute(
            text("SELECT status, ats_score FROM applications WHERE user_id=:u AND canonical_job_id=:j"),
            {"u": str(uid), "j": job_id},
        )).first()
    assert row is not None and row[0] == "Tailored"

    # render a PDF → signed ref → loadable within TTL
    rendered = await tailor_service.render_asset(uid, job_id, "pdf", "resume")
    ref = rendered["asset_ref"]
    found = load_asset(ref)
    assert found is not None
    path, content_type = found
    assert content_type == "application/pdf"
    assert path.read_bytes()[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_tailor_idempotency_cache():
    uid = uuid4()
    job_id = await _seed_job()
    await _seed_profile(uid)
    r1 = await tailor_service.tailor(uid, job_id, check_live=False)
    r2 = await tailor_service.tailor(uid, job_id, check_live=False)
    # same (user, job, profile_version) → identical selection (served from cache)
    assert r1["selection"] == r2["selection"]
