"""TTL cache unit test (docs/07 §2)."""

from __future__ import annotations

import time

from galaxy.common.cache import TTLCache


def test_ttl_cache_hit_miss_and_expiry():
    c = TTLCache(ttl_seconds=0.05, max_entries=10)
    assert c.get("k") is None  # miss
    c.set("k", {"v": 1})
    assert c.get("k") == {"v": 1}  # hit
    time.sleep(0.06)
    assert c.get("k") is None  # expired


def test_ttl_cache_evicts_when_full():
    c = TTLCache(ttl_seconds=100, max_entries=2)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)  # triggers eviction of the soonest-to-expire
    assert len(c._store) <= 2
    assert c.get("c") == 3
