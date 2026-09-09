"""Rate limiter + circuit breaker tests (docs/02 §5)."""

from __future__ import annotations

import asyncio
import time

import pytest

from galaxy.fetch.limits import CircuitOpen, SourceGovernor, TokenBucket
from galaxy.models.enums import Site


@pytest.mark.asyncio
async def test_token_bucket_throttles():
    bucket = TokenBucket(rate=20.0, capacity=2.0)  # 2 burst, then 20/s
    start = time.monotonic()
    # 2 immediate (burst) + 3 more that must wait ~ (3/20)s
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 3 / 20 * 0.8  # allow scheduling slack


@pytest.mark.asyncio
async def test_circuit_breaker_opens_then_cools_down():
    gov = SourceGovernor(default_rate=1000, breaker_threshold=3, breaker_cooldown=0.2)
    site = Site.LINKEDIN

    # 3 consecutive failures trip the breaker
    for _ in range(3):
        await gov.acquire(site)
        gov.record(site, ok=False)
    assert gov.is_open(site)

    # while open, acquire raises CircuitOpen
    with pytest.raises(CircuitOpen):
        await gov.acquire(site)

    # after cooldown it half-opens and allows a trial call
    await asyncio.sleep(0.25)
    await gov.acquire(site)  # should not raise
    gov.record(site, ok=True)
    assert not gov.is_open(site)


@pytest.mark.asyncio
async def test_breaker_is_per_site():
    gov = SourceGovernor(default_rate=1000, breaker_threshold=2, breaker_cooldown=5)
    for _ in range(2):
        await gov.acquire(Site.LINKEDIN)
        gov.record(Site.LINKEDIN, ok=False)
    assert gov.is_open(Site.LINKEDIN)
    # a different site is unaffected
    await gov.acquire(Site.GREENHOUSE)
    assert not gov.is_open(Site.GREENHOUSE)
