"""Normalizer-version backfill (docs/03 §2, docs/04 §3.1).

When NORMALIZER_VERSION bumps, existing canonical rows carry stale derived fields. This re-derives
them in place — no re-scrape needed, because the inputs (title + description_md) are already stored
on the row. It is idempotent and safe to run on every startup: rows already at the current version
are skipped by the WHERE clause.

v1 → v2 introduced jd_skills, so that is the field this recomputes. The other derived fields
(seniority / min_years / onsite / clearance) are unchanged across v1→v2 and were resolved per-source
at merge time, so they are intentionally left as-is rather than re-derived from the merged text.
"""

from __future__ import annotations

import json

import structlog
from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker
from galaxy.ingestion.normalize import NORMALIZER_VERSION, extract_jd_skills

log = structlog.get_logger(__name__)

_SELECT_STALE = text(
    """
    SELECT canonical_job_id, title, description_md
    FROM canonical_jobs
    WHERE normalizer_version IS DISTINCT FROM :version
    ORDER BY last_seen_at DESC
    LIMIT :batch
    """
)

_UPDATE_DERIVED = text(
    """
    UPDATE canonical_jobs
    SET jd_skills = CAST(:skills AS jsonb), normalizer_version = :version
    WHERE canonical_job_id = :cid
    """
)


async def backfill_jd_skills(batch_size: int = 500) -> int:
    """Re-derive jd_skills for every row below the current normalizer version. Returns count updated."""
    sm = get_sessionmaker()
    total = 0
    while True:
        async with sm() as session:
            rows = (
                await session.execute(
                    _SELECT_STALE, {"version": NORMALIZER_VERSION, "batch": batch_size}
                )
            ).all()
            if not rows:
                break
            for r in rows:
                skills = extract_jd_skills(r.title, r.description_md)
                await session.execute(
                    _UPDATE_DERIVED,
                    {
                        "skills": json.dumps(skills),
                        "version": NORMALIZER_VERSION,
                        "cid": r.canonical_job_id,
                    },
                )
            await session.commit()
            total += len(rows)
            if len(rows) < batch_size:
                break
    if total:
        log.info("backfill.jd_skills_done", updated=total, version=NORMALIZER_VERSION)
    return total
