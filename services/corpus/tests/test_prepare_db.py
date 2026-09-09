"""Phase W1 DB tests (docs/11): apply-prepare writes the resume + persists fill_context;
fill-context resolution prefers URL-host match over recency.

Skipped unless GALAXY_TEST_DB=1 with a live Postgres.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from galaxy.common.config import get_settings
from galaxy.db.engine import get_sessionmaker, migrate
from galaxy.dedup.engine import DedupEngine
from galaxy.generation import service as gen
from galaxy.ingestion.normalize import apply_derived_fields
from galaxy.ingestion.store import PostgresJobStore
from galaxy.models.enums import Site
from galaxy.models.job import Location, RawJobFields, SourceObservation
from galaxy.models.profile import Identity, Profile, Project, Skill
from galaxy.search import pipeline_store as ps
from galaxy.search.profiles import ProfileStore

pytestmark = pytest.mark.skipif(
    os.getenv("GALAXY_TEST_DB") != "1", reason="set GALAXY_TEST_DB=1 with a live Postgres"
)

NOW = datetime(2026, 7, 24, tzinfo=UTC)


async def _clean():
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        await s.execute(text("DELETE FROM applications"))
        await s.execute(text("DELETE FROM search_feedback"))
        await s.execute(text("DELETE FROM source_observations"))
        await s.execute(text("DELETE FROM canonical_jobs"))
        await s.execute(text("DELETE FROM profiles"))


def _seed_job(jid, title, company, url, desc):
    f = RawJobFields(
        title=title, company=company, location=Location(remote=True, country="USA"), description_md=desc
    )
    apply_derived_fields(f)
    obs = SourceObservation(
        site=Site.GREENHOUSE, source_job_id=jid, url=url, observed_at=NOW, raw_title=title, fields=f
    )
    return DedupEngine().dedup([obs]).jobs


def _profile(uid):
    return Profile(
        user_id=uid,
        identity=Identity(name="Ada Dev", email="ada@x.io", phone="555"),
        skills=[Skill(name="Python", tags=["software"])],
        projects=[Project(
            title="ETL", bullets=["Built an ETL pipeline in Python."],
            skills=["Python"], role_tags=["software"], verbatim=True,
        )],
        experience=[{"title": "SWE", "company": "Acme", "bullets": ["Shipped"], "tags": ["software"]}],
        anything_else="Authorized to work in the US.",
    )


@pytest.mark.asyncio
async def test_prepare_writes_resume_and_persists_fill_context(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_OUTPUT_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        await migrate()
        await _clean()
        uid = uuid4()
        await ProfileStore().upsert(_profile(uid))
        jobs = _seed_job("j1", "Python Engineer", "Acme",
                         "https://boards.greenhouse.io/acme/jobs/1", "Build APIs with Python and SQL.")
        await PostgresJobStore().upsert_jobs(jobs)
        jid = jobs[0].canonical_job_id

        ctx = await gen.prepare(uid, jid, check_live=False)

        assert ctx["title"] == "Python Engineer"
        assert ctx["identity"]["email"] == "ada@x.io"          # identity is fixed
        assert ctx["anything_else"].startswith("Authorized")   # anything-else is fixed
        assert any(p["title"] == "ETL" for p in ctx["projects"])
        assert ctx["experience"] and ctx["experience"][0]["title"] == "SWE"
        # resume written to the configured dir as a real PDF
        rp = Path(ctx["resume_path"])
        assert rp.exists() and rp.suffix == ".pdf" and str(tmp_path) in str(rp)
        assert rp.read_bytes()[:5] == b"%PDF-"

        sm = get_sessionmaker()
        async with sm() as s:
            row = (await s.execute(
                text("SELECT fill_context, resume_path FROM applications WHERE user_id = :u"),
                {"u": str(uid)},
            )).mappings().one()
        assert row["fill_context"]["title"] == "Python Engineer"
        assert row["resume_path"] == ctx["resume_path"]

        # re-prepare is idempotent (still one application row)
        await gen.prepare(uid, jid, check_live=False)
        async with sm() as s:
            n = (await s.execute(
                text("SELECT count(*) FROM applications WHERE user_id = :u"), {"u": str(uid)}
            )).scalar()
        assert n == 1
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_fill_context_url_match_beats_recency(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_OUTPUT_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        await migrate()
        await _clean()
        uid = uuid4()
        await ProfileStore().upsert(_profile(uid))
        a = _seed_job("a", "Role A", "Acme", "https://a-corp.com/jobs/1", "Python.")
        b = _seed_job("b", "Role B", "Beta", "https://b-corp.com/jobs/2", "Python.")
        await PostgresJobStore().upsert_jobs(a + b)

        await gen.prepare(uid, a[0].canonical_job_id, check_live=False)   # older
        await gen.prepare(uid, b[0].canonical_job_id, check_live=False)   # newer

        # no url → the most recently prepared (B)
        assert (await ps.get_fill_context(uid))["title"] == "Role B"
        # url on A's host → A wins despite B being newer
        assert (await ps.get_fill_context(uid, "https://a-corp.com/jobs/1"))["title"] == "Role A"
        # Never fill an unrelated tab with the newest application's answers.
        assert await ps.get_fill_context(uid, "https://unrelated.example/jobs/1") is None
    finally:
        get_settings.cache_clear()
