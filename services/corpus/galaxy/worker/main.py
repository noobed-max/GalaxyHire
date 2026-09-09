"""One-shot maintenance worker entrypoint (docs/01 §5).

Runs migrations then one embed + global-dedup pass, and exits. Invoked as
`python -m galaxy.worker.main`. For recurring maintenance use the Arq scheduler instead:
`arq galaxy.worker.scheduler.WorkerSettings`.

It used to chain a full adapter ingest (ATS + feeds + boards). That died with the
GalaxyJobsAi sources: collection is triggered by searching in the app (galaxy/scrape/
runner.py), so a bare worker run must never hit job boards — it only reconciles what is
already in the corpus.
"""

from __future__ import annotations

import asyncio

import structlog

from galaxy.db.engine import migrate
from galaxy.worker import tasks

log = structlog.get_logger(__name__)


async def run_maintenance() -> None:
    await migrate()
    emb = await tasks.embed_new()
    deduped = await tasks.dedup_global()
    log.info("worker.maintenance_done", **emb, **deduped)


if __name__ == "__main__":
    asyncio.run(run_maintenance())
