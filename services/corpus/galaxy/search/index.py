"""Embedding backfill (docs/01 §6A: "embedder → vectorize new/changed → write pgvector").

Finds canonical jobs with no embedding or a stale embedding_version and (re)embeds them in
batches. Idempotent; safe to run after every ingest pass.
"""

from __future__ import annotations

import structlog
from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker
from galaxy.search.embedder import Embedder, embedding_text, get_embedder, to_pgvector

log = structlog.get_logger(__name__)

_SELECT_PENDING = text(
    """
    SELECT canonical_job_id, title, description_md, jd_keywords
    FROM canonical_jobs
    WHERE embedding IS NULL OR embedding_version IS DISTINCT FROM :version
    ORDER BY last_seen_at DESC
    LIMIT :batch
    """
)

_UPDATE_EMBEDDING = text(
    """
    UPDATE canonical_jobs
    SET embedding = CAST(:vec AS vector), embedding_version = :version
    WHERE canonical_job_id = :cid
    """
)


async def embed_pending(embedder: Embedder | None = None, batch_size: int = 256) -> int:
    """Embed all jobs whose vector is missing or on a different version. Returns count embedded."""
    embedder = embedder or get_embedder()
    sm = get_sessionmaker()
    total = 0
    while True:
        async with sm() as session:
            rows = (
                await session.execute(_SELECT_PENDING, {"version": embedder.version, "batch": batch_size})
            ).all()
            if not rows:
                break
            texts = [embedding_text(r.title, r.description_md, r.jd_keywords or []) for r in rows]
            vectors = embedder.embed(texts)
            for r, vec in zip(rows, vectors, strict=True):
                await session.execute(
                    _UPDATE_EMBEDDING,
                    {"vec": to_pgvector(vec), "version": embedder.version, "cid": r.canonical_job_id},
                )
            await session.commit()  # commit the auto-begun transaction (SELECT + UPDATEs)
            total += len(rows)
            if len(rows) < batch_size:
                break
    log.info("embed.backfill_done", embedded=total, version=embedder.version)
    return total
