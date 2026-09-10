"""Destructive corpus maintenance: erase all stored corpus data for a factory reset.

The corpus (canonical jobs, source observations, applications, feedback, saved searches, corpus
profiles, and scrape history) is reusable product data: the app's data-only reset deliberately keeps
it, because rebuilding it costs hours of connector runs and every search shares it. The full factory
reset ("Delete everything" in the Danger zone) calls this so the install actually returns to an
empty state instead of keeping hundreds of scraped jobs behind.
"""

from __future__ import annotations

from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker
from galaxy.scrape import runner

# Children before parents: applications and source_observations reference canonical_jobs, and
# applications reference profiles. Deleting in this order keeps the counts honest (no silent
# cascade) and keeps working if a future foreign key loses its ON DELETE clause.
_TABLES_CHILD_FIRST = (
    "applications",
    "source_observations",
    "search_feedback",
    "saved_searches",
    "scrape_runs",
    "profiles",
    "canonical_jobs",
)


async def purge_corpus() -> dict:
    """Stop any running scrape, then delete every stored corpus row.

    Returns the per-table deleted counts plus whether a live scrape was stopped, so the Danger-zone
    summary can show the user exactly what was erased. The delete runs in one transaction: a
    factory reset either completes or leaves the previous state intact.
    """
    stopped = await runner.stop()
    deleted: dict[str, int] = {}
    sm = get_sessionmaker()
    async with sm() as s:
        for table in _TABLES_CHILD_FIRST:
            result = await s.execute(text(f"DELETE FROM {table}"))  # noqa: S608 - fixed table list
            deleted[table] = int(result.rowcount or 0)
        await s.commit()
    return {"purged": deleted, "stopped": stopped}
