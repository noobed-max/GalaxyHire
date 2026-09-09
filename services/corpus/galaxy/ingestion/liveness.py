"""Liveness check (docs/02 §4, docs/07 §3).

Zero-cost confirmation that a posting is still open before we spend generation effort. Best-effort
and fail-open: a page returning 404/410 or an obviously-gone body is `expired`; a transport error
does NOT mark a job dead (we don't want a flaky network to block tailoring).
"""

from __future__ import annotations

import structlog

from galaxy.fetch.client import FetchClient

log = structlog.get_logger(__name__)

_GONE_MARKERS = (
    "no longer accepting applications",
    "this job is no longer available",
    "position has been filled",
    "job not found",
    "posting is closed",
)


async def is_open(url: str) -> bool:
    """Return False only when we have positive evidence the posting is gone."""
    if not url:
        return True
    try:
        async with FetchClient() as client:
            resp = await client.get(url, retries=1)
    except Exception as exc:  # noqa: BLE001 — transport failure is inconclusive → treat as open
        log.info("liveness.inconclusive", url=url, error=str(exc))
        return True
    if resp.status in (404, 410):
        return False
    body = (resp.text or "").lower()
    if len(body) < 300:
        return False  # a stub page with no JD is a closed listing
    return not any(m in body for m in _GONE_MARKERS)
