from __future__ import annotations

import asyncio
import signal

from fastapi import APIRouter

from api.rate_limit import RateLimiter, require_rate_limit
from core.telemetry import log_error, redact_sensitive, redact_text


router = APIRouter(prefix="/api/v1", tags=["misc"])
_background_tasks: set[asyncio.Task] = set()


def _track_background_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


_errors_limiter = RateLimiter(30, 60)


def _clip(value: object, max_len: int = 4000) -> str:
    return str(value or "")[:max_len]


@router.post("/errors")
async def record_frontend_error(payload: dict):
    # Bound every field so a runaway/abusive client can't write multi-MB lines
    # into errors.jsonl (redact_sensitive truncates strings but not a giant
    # nested dict passed as componentStack).
    require_rate_limit(_errors_limiter)
    safe_payload = redact_sensitive({
        "error": _clip(payload.get("error") or "Frontend error", 2000),
        "componentStack": _clip(payload.get("componentStack", ""), 8000),
        "url": _clip(payload.get("url", ""), 1000),
        "userAgent": _clip(payload.get("userAgent", ""), 500),
    })
    log_error(redact_text(_clip(payload.get("error") or "Frontend error", 2000)), {"frontend": safe_payload})
    return {"ok": True}


@router.post("/shutdown")
async def request_shutdown():
    async def _shutdown_soon():
        await asyncio.sleep(0.1)
        signal.raise_signal(signal.SIGTERM)

    _track_background_task(asyncio.create_task(_shutdown_soon()))
    return {"ok": True}
