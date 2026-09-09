"""Ingestion surface + corpus counters (docs/02, docs/07). API-key guarded.

The only write path is /observations, fed by the Node scraper worker through the career-ops
providers. The old /ats and /search routes drove the GalaxyJobsAi Python adapters and were
deleted with them; the worker must not be able to post through a route that runs nothing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from galaxy.api.deps import require_api_key
from galaxy.api.throttle import throttle
from galaxy.ingestion.pipeline import IngestPipeline
from galaxy.ingestion.store import PostgresJobStore
from galaxy.models.job import SourceObservation

router = APIRouter(
    prefix="/ingest", tags=["ingest"],
    dependencies=[Depends(require_api_key), Depends(throttle)],
)


class IngestResponse(BaseModel):
    fetched: int
    upserted: int
    canonical_out: int
    split_groups: int
    failures: int


class ObservationIngestRequest(BaseModel):
    """A batch of observations fetched by an out-of-process worker.

    Mirrors `EnvelopeBatch` in `packages/contract`. `observations` is typed as the domain model
    directly, so FastAPI/pydantic validates the envelope for us and a malformed batch is rejected
    at the boundary with a 422 naming the offending field.
    """

    worker: str = "scraper-node"
    observations: list[SourceObservation]


@router.post("/observations", response_model=IngestResponse)
async def ingest_observations(req: ObservationIngestRequest) -> IngestResponse:
    """Accept observations the Node scraper worker already fetched (ARCHITECTURE.md D4).

    The career-ops providers run in Node (their native runtime), so they cannot be routed
    through a Python fetcher. They post here, land in the shared dedup + upsert path, and the
    corpus holds one deduplicated view regardless of which runtime did the fetching.
    """
    report = await IngestPipeline(PostgresJobStore()).run_observations(
        req.observations, worker=req.worker
    )
    return IngestResponse(
        fetched=report.fetched,
        upserted=report.upserted,
        canonical_out=report.dedup.canonical_out if report.dedup else 0,
        split_groups=report.dedup.split_groups if report.dedup else 0,
        failures=len(report.failures),
    )


class EmbedResponse(BaseModel):
    embedded: int


@router.post("/embed", response_model=EmbedResponse)
async def embed(batch_size: int = 256) -> EmbedResponse:
    """Vectorize jobs whose embedding is missing or on an older version.

    Ingest deliberately does *not* embed inline: a broad scrape posts tens of thousands of
    observations in batches, and embedding each batch inside its request would make the posts slow
    enough to time out. But retrieval filters on `embedding_version`, so an unembedded job is
    invisible to search — which makes "post then forget" a trap.

    The scheduler already runs this every 15 minutes. Exposing it lets a standalone scrape make its
    own results searchable immediately, without requiring redis and arq to be up. `embed_pending`
    is idempotent, so calling this more often than needed costs a query and nothing else.
    """
    from galaxy.search.index import embed_pending

    return EmbedResponse(embedded=await embed_pending(batch_size=batch_size))


@router.get("/count")
async def job_count() -> dict:
    """Corpus size, plus how much of it is actually searchable.

    The two numbers differ whenever jobs have been ingested but not yet embedded, which is the
    normal state immediately after a scrape.
    """
    from sqlalchemy import text

    from galaxy.common.config import get_settings
    from galaxy.db.engine import get_sessionmaker

    total = await PostgresJobStore().count()
    async with get_sessionmaker()() as session:
        searchable = (
            await session.execute(
                text("SELECT count(*) FROM canonical_jobs WHERE embedding_version = :v"),
                {"v": get_settings().embedding_version},
            )
        ).scalar_one()
    return {"canonical_jobs": total, "searchable": searchable, "pending_embedding": total - searchable}


@router.get("/metrics")
async def ingest_metrics() -> dict:
    """Per-source ingest counters + block rates (docs/08 §5). Rising block_rate = a defended source."""
    from galaxy.common.metrics import snapshot

    return snapshot()
