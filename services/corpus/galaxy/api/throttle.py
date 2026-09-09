"""Per-API-key rate limiting (docs/07 §2 ThrottlerGuard).

A non-blocking token bucket per key: over the limit → 429. In-memory for v1 (single API
process); a Redis-backed bucket implements the same check when the API scales out.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from galaxy.fetch.limits import TokenBucket

# ~5 req/s steady, burst up to 60 — generous for one extension client, stops runaway loops.
_RATE = 5.0
_CAPACITY = 60.0
_buckets: dict[str, TokenBucket] = {}


def _bucket(key: str) -> TokenBucket:
    if key not in _buckets:
        _buckets[key] = TokenBucket(rate=_RATE, capacity=_CAPACITY)
    return _buckets[key]


async def throttle(x_api_key: str | None = Header(default=None)) -> None:
    """Reject with 429 when the caller exceeds its per-key rate (runs after the api-key guard)."""
    if not _bucket(x_api_key or "anon").try_acquire():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded — slow down",
            headers={"Retry-After": "1"},
        )
