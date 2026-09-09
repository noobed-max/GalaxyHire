"""Async SQLAlchemy engine + session + migration runner (docs/07)."""

from __future__ import annotations

from pathlib import Path

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from galaxy.common.config import get_settings

log = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker | None = None

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def migrate() -> None:
    """Apply SQL migrations in order. Adds the HNSW vector index only if pgvector >= 0.8."""
    engine = get_engine()
    async with engine.begin() as conn:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            log.info("migrate.apply", file=path.name)
            sql = path.read_text()
            # asyncpg can't run multiple statements in one exec; split on semicolons at line end
            for stmt in _split_statements(sql):
                await conn.execute(text(stmt))

        # HNSW needs pgvector >= 0.8 for iterative scans (docs/04 §2.2); create it opportunistically
        ver_q = text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        version = (await conn.execute(ver_q)).scalar()
        if version and _version_ge(version, "0.8.0"):
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_cj_embedding_hnsw "
                    "ON canonical_jobs USING hnsw (embedding vector_cosine_ops)"
                )
            )
            log.info("migrate.hnsw_index_created", pgvector=version)
        else:
            log.warning("migrate.hnsw_skipped", pgvector=version, need=">=0.8.0")


def _split_statements(sql: str) -> list[str]:
    out, buf, depth = [], [], 0
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        buf.append(line)
        depth += line.count("(") - line.count(")")
        if stripped.endswith(";") and depth <= 0:
            out.append("\n".join(buf).rstrip().rstrip(";"))
            buf, depth = [], 0
    if buf:
        out.append("\n".join(buf).rstrip().rstrip(";"))
    return [s for s in out if s.strip()]


def _version_ge(a: str, b: str) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split(".")[:3])

    return parts(a) >= parts(b)


async def ping() -> bool:
    engine = get_engine()
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT 1"))).scalar() == 1


async def reset_engine() -> None:
    """Dispose and clear the cached engine. Used in tests so each function-scoped event loop
    gets a fresh engine (asyncpg connections are bound to the loop that created them)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
