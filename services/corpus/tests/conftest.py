"""Shared test helpers: a fake fetch client that serves fixtures (no network).

`ctx_for`/FetchCtx were removed with the Python adapters; liveness tests use FakeClient directly."""

from __future__ import annotations

import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def _reset_db_engine():
    """Dispose the global async engine after each test so the next test's event loop gets a
    fresh one (asyncpg connections are bound to their creating loop). No-op when unused."""
    yield
    from galaxy.db.engine import reset_engine

    await reset_engine()


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self._payload = payload
        self.text = text
        self.url = ""

    @property
    def ok(self):
        return 200 <= self.status < 400

    def json(self):
        return self._payload


class FakeClient:
    """Serves a fixed payload (json) or text for every request; records call URLs."""

    def __init__(self, payload=None, text="", status=200):
        self._payload = payload
        self._text = text
        self._status = status
        self.calls: list[str] = []

    async def get(self, url, *, params=None, headers=None, retries=3):
        self.calls.append(url)
        return FakeResp(self._status, self._payload, self._text)

    async def post(self, url, *, json=None, headers=None, retries=3):
        self.calls.append(url)
        return FakeResp(self._status, self._payload, self._text)
