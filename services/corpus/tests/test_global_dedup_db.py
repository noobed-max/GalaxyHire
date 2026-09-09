"""Global cross-run dedup integration test (docs/03 §2).

Skipped unless GALAXY_TEST_DB=1 with a live Postgres.
Run: make db-up && GALAXY_TEST_DB=1 uv run pytest tests/test_global_dedup_db.py
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker, migrate
from galaxy.dedup.engine import DedupEngine
from galaxy.dedup.global_dedup import run_global_dedup
from galaxy.ingestion.normalize import apply_derived_fields
from galaxy.ingestion.store import PostgresJobStore
from galaxy.models.enums import Site
from galaxy.models.job import Location, RawJobFields, SourceObservation

pytestmark = pytest.mark.skipif(
    os.getenv("GALAXY_TEST_DB") != "1", reason="set GALAXY_TEST_DB=1 with a live Postgres"
)

NOW = datetime(2026, 7, 24, tzinfo=UTC)
DESC = "Build and ship ML models with Python and PyTorch. Lead the data science team and mentor engineers."
OTHER_DESC = "Own our React and TypeScript design system. Ship accessible UI components for the web app."


async def _clean():
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        await s.execute(text("DELETE FROM applications"))
        await s.execute(text("DELETE FROM search_feedback"))
        await s.execute(text("DELETE FROM source_observations"))
        await s.execute(text("DELETE FROM canonical_jobs"))


def _obs(site, sid, title, company, desc, *, remote=False, city=None):
    f = RawJobFields(
        title=title, company=company,
        location=Location(city=city, country="USA", remote=remote), description_md=desc,
    )
    apply_derived_fields(f)
    return SourceObservation(
        site=site, source_job_id=sid, url=f"https://x/{sid}", observed_at=NOW, raw_title=title, fields=f
    )


def _store_one(site, sid, title, company, desc, *, remote=False, city=None):
    """Ingest a single observation as its own canonical job (simulates one scrape run)."""
    return DedupEngine().dedup([_obs(site, sid, title, company, desc, remote=remote, city=city)]).jobs


async def _count() -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        return (await s.execute(text("SELECT count(*) FROM canonical_jobs"))).scalar()


@pytest.mark.asyncio
async def test_global_dedup_merges_cross_run_near_duplicate():
    await migrate()
    await _clean()
    store = PostgresJobStore()

    # same role scraped in two runs — one says Remote, the other San Francisco → DIFFERENT base
    # keys → two canonical ids the in-batch dedup never compares.
    a = _store_one(Site.GREENHOUSE, "a1", "Staff Data Scientist", "Acme", DESC, remote=True)
    b = _store_one(Site.LEVER, "b1", "Staff Data Scientist", "Acme", DESC, city="San Francisco")
    await store.upsert_jobs(a + b)
    assert a[0].canonical_job_id != b[0].canonical_job_id
    assert await _count() == 2

    stats = await run_global_dedup()
    assert stats.clusters_merged == 1
    assert stats.jobs_removed == 1
    assert await _count() == 1

    sm = get_sessionmaker()
    async with sm() as s:
        # survivor now carries BOTH source observations, and its vector was nulled to force re-embed
        row = (await s.execute(text(
            "SELECT canonical_job_id, embedding, "
            "(SELECT count(*) FROM source_observations o WHERE o.canonical_job_id = c.canonical_job_id) n "
            "FROM canonical_jobs c"
        ))).mappings().one()
        assert row["n"] == 2
        assert row["embedding"] is None


@pytest.mark.asyncio
async def test_global_dedup_keeps_genuinely_distinct_roles():
    await migrate()
    await _clean()
    # two distinct roles, same company+title+location but very different descriptions. The in-batch
    # SimHash guard already splits them into two canonical ids; the global pass must NOT re-merge
    # them (their descriptions are SimHash-far apart).
    obs = [
        _obs(Site.GREENHOUSE, "c1", "Software Engineer", "Acme", DESC, city="Austin"),
        _obs(Site.GREENHOUSE, "c2", "Software Engineer", "Acme", OTHER_DESC, city="Austin"),
    ]
    jobs = DedupEngine().dedup(obs).jobs
    assert len(jobs) == 2  # split by description similarity
    await PostgresJobStore().upsert_jobs(jobs)
    assert await _count() == 2

    stats = await run_global_dedup()
    assert stats.jobs_removed == 0
    assert await _count() == 2
