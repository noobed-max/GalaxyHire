"""Maintenance tasks for the corpus (docs/02 §4).

The adapter-driven ingestion tasks (`ingest_ats` / `ingest_feeds` / `ingest_boards` /
`full_pass`) were deleted with the GalaxyJobsAi Python adapters. Collection is no longer a
background sweep: searching in the app triggers a career-ops scrape (galaxy/scrape/runner.py),
and the results arrive via /ingest/observations. What still earns a recurring slot is the
idempotent post-processing the scrape path depends on: embedding, and the global cross-run
dedup that catches near-duplicates arriving in *different* batches.
"""

from __future__ import annotations

from galaxy.search.index import embed_pending


async def embed_new() -> dict:
    """Vectorize jobs missing an embedding or on a stale version (docs/01 §6A)."""
    return {"embedded": await embed_pending()}


async def dedup_global() -> dict:
    """Global cross-run near-duplicate merge over the whole DB (docs/03 §2).

    Also called at the end of every scrape (runner finalize), so the nightly pass here is a
    safety net for rows the per-run pass missed (e.g. after a crash), not the primary mechanism.
    """
    from dataclasses import asdict

    from galaxy.dedup.global_dedup import run_global_dedup

    return asdict(await run_global_dedup())
