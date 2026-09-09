"""Per-source politeness: token-bucket rate limiter + circuit breaker (docs/02 §5, §08).

Both are keyed by Site so one defended board can't starve or break the others. In-process for
v1 (single worker); a Redis-backed version slots in behind the same interface when the worker
fans out across processes.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import structlog

from galaxy.models.enums import Site

log = structlog.get_logger(__name__)


class TokenBucket:
    """Async token bucket: `rate` tokens/sec, burst up to `capacity`."""

    def __init__(self, rate: float, capacity: float | None = None):
        self.rate = rate
        self.capacity = capacity if capacity is not None else max(1.0, rate)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                await asyncio.sleep(deficit / self.rate)

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking take: consume a token if available, else return False (for API throttling)."""
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False


class CircuitOpen(Exception):
    """Raised when a source's breaker is open (auto-disabled after repeated failures)."""


@dataclass
class _Breaker:
    threshold: int
    cooldown: float
    failures: int = 0
    opened_at: float | None = None

    def before(self) -> None:
        if self.opened_at is not None:
            if time.monotonic() - self.opened_at < self.cooldown:
                raise CircuitOpen
            # cooldown elapsed → half-open: allow a trial call
            self.opened_at = None
            self.failures = 0

    def on_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def on_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()


@dataclass
class SourceGovernor:
    """Owns the rate limiter + circuit breaker for every Site (docs/02 §5)."""

    default_rate: float = 1.0
    default_capacity: float = 3.0
    breaker_threshold: int = 5
    breaker_cooldown: float = 300.0
    # per-site overrides: hot boards get slower rates
    rates: dict[Site, float] = field(default_factory=dict)
    _buckets: dict[Site, TokenBucket] = field(default_factory=dict)
    _breakers: dict[Site, _Breaker] = field(default_factory=dict)

    def _bucket(self, site: Site) -> TokenBucket:
        if site not in self._buckets:
            rate = self.rates.get(site, self.default_rate)
            self._buckets[site] = TokenBucket(rate=rate, capacity=self.default_capacity)
        return self._buckets[site]

    def _breaker(self, site: Site) -> _Breaker:
        if site not in self._breakers:
            self._breakers[site] = _Breaker(self.breaker_threshold, self.breaker_cooldown)
        return self._breakers[site]

    async def acquire(self, site: Site) -> None:
        """Raise CircuitOpen if the source is tripped, else wait for a rate token."""
        self._breaker(site).before()
        await self._bucket(site).acquire()

    def record(self, site: Site, ok: bool) -> None:
        breaker = self._breaker(site)
        if ok:
            breaker.on_success()
        else:
            breaker.on_failure()
            if breaker.opened_at is not None:
                log.warning("source.circuit_open", site=site.value, failures=breaker.failures)

    def is_open(self, site: Site) -> bool:
        b = self._breakers.get(site)
        return bool(b and b.opened_at is not None)
