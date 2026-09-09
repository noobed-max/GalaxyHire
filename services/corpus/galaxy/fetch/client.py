"""HTTP fetch client with the escalation ladder's lower rungs (docs/08 §2, §3).

Rung ① plain GET and rung ② TLS-impersonated GET are covered here via curl_cffi
(`impersonate="chrome"`), which matches a real browser's TLS/JA3 handshake. Proxy rotation is
per-session (docs/08 §3). camoufox rungs ④–⑤ live in a separate pool module and are optional.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass
from typing import Any

import structlog
from curl_cffi.requests import AsyncSession

log = structlog.get_logger(__name__)


@dataclass
class FetchResponse:
    status: int
    text: str
    url: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400

    def json(self) -> Any:
        import json

        return json.loads(self.text)


class FetchClient:
    """Async TLS-impersonating HTTP client with per-session proxy rotation.

    One `FetchClient` == one session (stable IP + cookie jar), so cookie/CSRF bootstrap flows
    keep affinity (docs/08 §3). Rotate by constructing a new client from the pool between sessions.
    """

    def __init__(
        self,
        proxy: str | None = None,
        impersonate: str = "chrome",
        # total-operation timeout. Big ATS boards (Spotify/Databricks ≈ 1 MB of JSON) take >20s on
        # a slow link and would be cut off mid-body, wasting all their bytes on every retry.
        timeout: float = 60.0,
        default_headers: dict[str, str] | None = None,
    ):
        self.proxy = proxy
        self.impersonate = impersonate
        self.timeout = timeout
        self.default_headers = default_headers or {}
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> FetchClient:
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        self._session = AsyncSession(
            impersonate=self.impersonate,
            timeout=self.timeout,
            proxies=proxies,
            headers=self.default_headers,
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def get(
        self, url: str, *, params: dict | None = None, headers: dict | None = None, retries: int = 3
    ) -> FetchResponse:
        return await self._request("GET", url, params=params, headers=headers, retries=retries)

    async def post(
        self, url: str, *, json: dict | None = None, headers: dict | None = None, retries: int = 3
    ) -> FetchResponse:
        return await self._request("POST", url, json=json, headers=headers, retries=retries)

    async def _request(
        self, method: str, url: str, *, params=None, json=None, headers=None, retries=3
    ) -> FetchResponse:
        assert self._session is not None, "use FetchClient as an async context manager"
        backoff = 1.0
        last_status = 0
        for attempt in range(retries):
            try:
                resp = await self._session.request(
                    method, url, params=params, json=json, headers=headers
                )
                last_status = resp.status_code
                if resp.status_code in (429, 500, 502, 503, 504):
                    log.warning("fetch.retryable", url=url, status=resp.status_code, attempt=attempt)
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                return FetchResponse(status=resp.status_code, text=resp.text, url=str(resp.url))
            except Exception as exc:  # noqa: BLE001 — network errors are retried
                log.warning("fetch.error", url=url, error=str(exc), attempt=attempt)
                await asyncio.sleep(backoff)
                backoff *= 2
        return FetchResponse(status=last_status or 599, text="", url=url)


class ProxyPool:
    """Round-robin over the configured proxy pool, rotating per SESSION not per request."""

    def __init__(self, proxies: list[str]):
        # empty string sentinel = direct (no proxy), always a valid dev pool member
        self._pool = proxies or [""]
        self._cycle = itertools.cycle(self._pool)

    def next(self) -> str | None:
        p = next(self._cycle)
        return p or None
