"""Arq scheduler — corpus maintenance cadences (docs/02 §4, docs/09).

Run with:  uv run arq galaxy.worker.scheduler.WorkerSettings

Collection is no longer scheduled: a search in the app triggers the career-ops scrape
(galaxy/scrape/runner.py) and the worker embeds on completion. What remains here is the
safety net for work that path can leave unfinished —

  - embed  : every 15 min, in case a scrape died between post and embed (idempotent sweep)
  - dedup  : nightly global merge, in case the end-of-run pass in the scrape finalizer missed
             rows (crash mid-finalize) or drift across days deserves one wide reconciliation

On startup it applies migrations and re-derives normalizer-versioned fields, then runs one
embed + dedup pass so a fresh deploy is consistent without waiting for the first cron tick.
It deliberately does NOT scrape: corpus warmth must never hammer job boards unattended
(the reason the app-triggered model exists).
"""

from __future__ import annotations

import structlog
from arq import cron
from arq.connections import RedisSettings

from galaxy.common.config import get_settings
from galaxy.db.engine import migrate
from galaxy.ingestion.backfill import backfill_jd_skills
from galaxy.worker import tasks

log = structlog.get_logger(__name__)


# --- task wrappers (arq calls take a ctx first arg) -------------------------


async def cron_embed(ctx) -> dict:
    r = await tasks.embed_new()
    log.info("cron.embed", **r)
    return r


async def cron_dedup_global(ctx) -> dict:
    r = await tasks.dedup_global()
    log.info("cron.dedup_global", **r)
    return r


async def _startup(ctx) -> None:
    await migrate()
    # re-derive normalizer-versioned fields on stale rows after a version bump (idempotent)
    backfilled = await backfill_jd_skills()
    log.info("scheduler.startup", jd_skills_backfilled=backfilled)
    emb = await tasks.embed_new()
    log.info("scheduler.initial_embed", **emb)
    deduped = await tasks.dedup_global()
    log.info("scheduler.initial_dedup", **deduped)


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = _startup
    functions = [cron_embed, cron_dedup_global]
    cron_jobs = [
        cron(cron_embed, minute={0, 15, 30, 45}),  # every 15 min
        cron(cron_dedup_global, hour=3, minute=20),  # nightly
    ]
