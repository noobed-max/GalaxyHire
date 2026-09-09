"""Health endpoints (docs/07 §2) — unauthenticated."""

from __future__ import annotations

from fastapi import APIRouter

from galaxy.db.engine import ping

router = APIRouter(tags=["health"])


@router.get("/ping")
async def ping_route() -> dict:
    return {"status": "ok"}


@router.get("/health")
async def health_route() -> dict:
    db_ok = False
    try:
        db_ok = await ping()
    except Exception:  # noqa: BLE001 — health must report, not raise
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok}
