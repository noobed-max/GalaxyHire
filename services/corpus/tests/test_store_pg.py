"""Postgres store integration test — real DB round-trip (docs/03 §6).

Skipped automatically unless a database is reachable (GALAXY_TEST_DB=1 and a live Postgres).
Run locally with:  make db-up && GALAXY_TEST_DB=1 uv run pytest tests/test_store_pg.py
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker, migrate
from galaxy.dedup.engine import DedupEngine
from galaxy.ingestion.normalize import apply_derived_fields
from galaxy.ingestion.store import PostgresJobStore
from galaxy.models.enums import Site
from galaxy.models.job import Location, RawJobFields, SourceObservation

pytestmark = pytest.mark.skipif(
    os.getenv("GALAXY_TEST_DB") != "1", reason="set GALAXY_TEST_DB=1 with a live Postgres"
)

NOW = datetime(2026, 7, 23, tzinfo=UTC)
DESC = "Senior Backend Engineer. Requires 5+ years of experience with Python and Postgres. " * 3


def _obs(site: Site, jid: str) -> SourceObservation:
    fields = RawJobFields(title="Senior Backend Engineer", company="Acme, Inc.",
                          location=Location(city="Austin", country="USA"), description_md=DESC)
    apply_derived_fields(fields)  # adapters do this at ingest (docs/02 §3.1)
    return SourceObservation(
        site=site, source_job_id=jid, url=f"https://{site.value}/{jid}", observed_at=NOW,
        raw_title="Senior Backend Engineer", fields=fields,
    )


@pytest.mark.asyncio
async def test_upsert_and_provenance_roundtrip():
    await migrate()
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        await s.execute(text("DELETE FROM applications"))  # FK → canonical_jobs
        await s.execute(text("DELETE FROM source_observations"))
        await s.execute(text("DELETE FROM canonical_jobs"))

    result = DedupEngine().dedup([_obs(Site.GREENHOUSE, "gh-9"), _obs(Site.LEVER, "lv-9")])
    assert len(result.jobs) == 1  # merged

    store = PostgresJobStore()
    assert await store.upsert_jobs(result.jobs) == 1
    assert await store.count() == 1

    # both observations landed and point at the one canonical job
    async with sm() as s:
        rows = (await s.execute(text("SELECT site FROM source_observations ORDER BY site"))).all()
        assert {r[0] for r in rows} == {"greenhouse", "lever"}
        # derived field persisted, and full-text index is populated
        row = (
            await s.execute(
                text(
                    "SELECT min_years_experience, search_tsv IS NOT NULL, "
                    "search_tsv_weighted IS NOT NULL FROM canonical_jobs"
                )
            )
        ).one()
        assert row[0] == 5
        assert row[1] is True
        assert row[2] is True

    # re-upsert is idempotent (upsert on conflict), still one row
    await store.upsert_jobs(result.jobs)
    assert await store.count() == 1
