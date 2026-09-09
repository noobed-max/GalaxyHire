from __future__ import annotations

import os
from pydantic import BaseModel
import pytest

from data.sqlite.settings import delete_setting, get_settings, save_settings
from llm.client import (
    _extract_and_validate_json,
    get_custom_config,
    resolve_custom_endpoints,
)


class SampleModel(BaseModel):
    summary: str
    score: int


def test_resolve_custom_endpoints_chat_completions():
    target, base = resolve_custom_endpoints("https://opencode.ai/zen/go/v1/chat/completions", "chat/completions")
    assert target == "https://opencode.ai/zen/go/v1/chat/completions"
    assert base == "https://opencode.ai/zen/go/v1"

    target2, base2 = resolve_custom_endpoints("https://opencode.ai/zen/go/v1", "chat/completions")
    assert target2 == "https://opencode.ai/zen/go/v1/chat/completions"
    assert base2 == "https://opencode.ai/zen/go/v1"


def test_resolve_custom_endpoints_responses():
    target, base = resolve_custom_endpoints("https://opencode.ai/zen/go/v1/responses", "responses")
    assert target == "https://opencode.ai/zen/go/v1/responses"
    assert base == "https://opencode.ai/zen/go/v1"

    target2, base2 = resolve_custom_endpoints("https://opencode.ai/zen/go/v1", "responses")
    assert target2 == "https://opencode.ai/zen/go/v1/responses"
    assert base2 == "https://opencode.ai/zen/go/v1"


def test_resolve_custom_endpoints_messages():
    target, base = resolve_custom_endpoints("https://opencode.ai/zen/go/v1/messages", "messages")
    assert target == "https://opencode.ai/zen/go/v1/messages"
    assert base == "https://opencode.ai/zen/go/v1"

    target2, base2 = resolve_custom_endpoints("https://opencode.ai/zen/go/v1", "messages")
    assert target2 == "https://opencode.ai/zen/go/v1/messages"
    assert base2 == "https://opencode.ai/zen/go/v1"


def test_get_custom_config_defaults():
    cfg = get_custom_config()
    assert cfg["endpoint_type"] in ("chat/completions", "responses", "messages")
    assert cfg["protocol"] in ("openai", "anthropic")


def test_extract_and_validate_json():
    # 1. Plain json
    m1 = _extract_and_validate_json('{"summary": "Fit", "score": 90}', SampleModel)
    assert m1.summary == "Fit"
    assert m1.score == 90

    # 2. Markdown fenced json with preamble
    raw2 = "Here is your output:\n```json\n{\"summary\": \"Great candidate\", \"score\": 95}\n```\nHope this helps!"
    m2 = _extract_and_validate_json(raw2, SampleModel)
    assert m2.summary == "Great candidate"
    assert m2.score == 95


def test_delete_setting(monkeypatch):
    class FakeCursor:
        def __init__(self, rowcount: int):
            self.rowcount = rowcount

    class FakeConn:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=()):
            self.executed.append((sql, params))
            if "DELETE FROM settings" in sql and params and params[0] == "custom_api_key":
                return FakeCursor(1)
            return FakeCursor(0)

        def commit(self):
            pass

        def close(self):
            pass

    fake_conn = FakeConn()
    monkeypatch.setattr("data.sqlite.settings._ensure_settings_table", lambda *a, **kw: None)
    monkeypatch.setattr("data.sqlite.settings.get_connection", lambda *a, **kw: fake_conn)

    assert delete_setting("custom_api_key") is True
    assert any("DELETE FROM settings WHERE key" in sql and params == ("custom_api_key",) for sql, params in fake_conn.executed)
    assert delete_setting("non_existent_key") is False


@pytest.mark.asyncio
async def test_probe_custom_missing_model():
    from api.routers.settings import probe_provider_key
    res = await probe_provider_key("custom", "sk-123", {"custom_base_url": "https://example.com/v1", "custom_model": ""})
    assert res["status"] == "missing_model"
    assert "model name" in res["detail"].lower()


@pytest.mark.asyncio
async def test_probe_custom_missing_url():
    from api.routers.settings import probe_provider_key
    res = await probe_provider_key("custom", "sk-123", {"custom_base_url": "", "custom_model": "test-m"})
    assert res["status"] == "endpoint_not_found"


@pytest.mark.asyncio
async def test_probe_custom_success(monkeypatch):
    import httpx
    from api.routers.settings import probe_provider_key

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "pong response"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, headers=None, json=None):
            self.last_url = url
            self.last_json = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeClient())

    res = await probe_provider_key("custom", "sk-123", {
        "custom_base_url": "https://example.com/v1/chat/completions",
        "custom_model": "my-model",
        "custom_endpoint_type": "chat/completions",
    })
    assert res["status"] == "ok"
    assert res["model"] == "my-model"
    assert "pong response" in res["reply"]


@pytest.mark.asyncio
async def test_probe_custom_model_not_found(monkeypatch):
    import httpx
    from api.routers.settings import probe_provider_key

    class FakeResponse:
        status_code = 401

        def json(self):
            return {"error": {"type": "ModelError", "message": "Model my-model is not supported"}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, headers=None, json=None):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeClient())

    res = await probe_provider_key("custom", "sk-123", {
        "custom_base_url": "https://example.com/v1",
        "custom_model": "my-model",
    })
    assert res["status"] == "model_not_found"
    assert "my-model is not supported" in res["detail"]
