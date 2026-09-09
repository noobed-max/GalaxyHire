"""Tiny async TTL cache (docs/07 §2 CacheService).

In-memory for v1 (single worker), fronted by a dict with per-key expiry. A Redis-backed drop-in
implements the same get/set interface when the worker fans out across processes.
"""

from __future__ import annotations

import time
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: float = 120.0, max_entries: int = 512):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if item is None:
            return None
        expiry, value = item
        if time.monotonic() > expiry:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self.max_entries:
            # evict the soonest-to-expire entry (cheap approximation of LRU)
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (time.monotonic() + self.ttl, value)

    def clear(self) -> None:
        self._store.clear()


search_cache = TTLCache(ttl_seconds=120.0)
