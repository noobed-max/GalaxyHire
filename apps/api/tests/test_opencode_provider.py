from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from api.routers.settings import list_provider_models, probe_provider_key
from data.sqlite.settings import validate_setting
from llm import client


class Result(BaseModel):
    answer: str


class FakeResponse:
    status_code = 200

    def __init__(self, data: dict):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


def _settings(monkeypatch, values: dict[str, str]):
    monkeypatch.setattr(client, "get_setting", lambda key, default="": values.get(key, default))


def test_opencode_is_a_separate_allowed_provider():
    for setting in (
        "llm_provider",
        "scout_provider",
        "evaluator_provider",
        "generator_provider",
        "ingestor_provider",
        "actuator_provider",
    ):
        assert validate_setting(setting, "opencode") == (True, "")


def test_official_go_custom_settings_migrate_to_dedicated_provider(tmp_path):
    from data.sqlite.connection import close_all, run_migrations

    db_path = str(tmp_path / "legacy-opencode.db")
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, val TEXT)")
    connection.executemany(
        "INSERT INTO settings(key, val) VALUES(?, ?)",
        [
            ("llm_provider", "custom"),
            ("custom_api_key", "legacy-test-key"),
            ("custom_base_url", "https://opencode.ai/zen/go/v1/"),
            ("custom_model", "muse-spark-1.3-contributor"),
            ("custom_protocol", "openai"),
            ("custom_endpoint_type", "responses"),
            ("custom_reasoning_effort", "medium"),
        ],
    )
    connection.commit()
    connection.close()

    try:
        run_migrations(db_path)
        connection = sqlite3.connect(db_path)
        migrated = dict(connection.execute("SELECT key, val FROM settings").fetchall())
        connection.close()
    finally:
        close_all()

    assert migrated["llm_provider"] == "opencode"
    assert migrated["opencode_api_key"] == "legacy-test-key"
    assert migrated["opencode_base_url"] == "https://opencode.ai/zen/go/v1/"
    assert migrated["opencode_model"] == "muse-spark-1.3-contributor"
    assert migrated["opencode_endpoint_type"] == "responses"
    assert migrated["opencode_reasoning_effort"] == "medium"
    assert migrated["custom_api_key"] == "legacy-test-key"


def test_non_opencode_custom_settings_do_not_migrate(tmp_path):
    from data.sqlite.connection import close_all, run_migrations

    db_path = str(tmp_path / "ordinary-custom.db")
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, val TEXT)")
    connection.executemany(
        "INSERT INTO settings(key, val) VALUES(?, ?)",
        [
            ("llm_provider", "custom"),
            ("custom_api_key", "ordinary-test-key"),
            ("custom_base_url", "https://llm.example.com/v1"),
        ],
    )
    connection.commit()
    connection.close()

    try:
        run_migrations(db_path)
        connection = sqlite3.connect(db_path)
        migrated = dict(connection.execute("SELECT key, val FROM settings").fetchall())
        connection.close()
    finally:
        close_all()

    assert migrated["llm_provider"] == "custom"
    assert "opencode_api_key" not in migrated
    assert "opencode_base_url" not in migrated


def test_opencode_resolves_its_own_key_model_and_environment(monkeypatch):
    values = {
        "llm_provider": "opencode",
        "opencode_api_key": "open-code-key",
        "opencode_model": "muse-spark-1.3-contributor",
        # A configured custom provider must not affect OpenCode.
        "custom_api_key": "custom-key",
        "custom_model": "custom-model",
    }
    _settings(monkeypatch, values)
    assert client.resolve_config() == (
        "opencode",
        "open-code-key",
        "muse-spark-1.3-contributor",
    )

    values["opencode_api_key"] = ""
    monkeypatch.setenv("OPENCODE_API_KEY", "environment-key")
    assert client.resolve_config()[1] == "environment-key"


def test_opencode_config_defaults_to_go_responses_and_isolated(monkeypatch):
    _settings(
        monkeypatch,
        {
            "custom_base_url": "https://custom.example/v1/messages",
            "custom_endpoint_type": "messages",
            "custom_reasoning_effort": "high",
        },
    )
    cfg = client.get_opencode_config()
    assert cfg == {
        "url": "https://opencode.ai/zen/go/v1",
        "protocol": "openai",
        "endpoint_type": "responses",
        "reasoning": "",
    }


def test_opencode_anthropic_protocol_selects_messages_when_format_is_unset(monkeypatch):
    _settings(
        monkeypatch,
        {
            "opencode_base_url": "https://opencode.ai/zen/go/v1",
            "opencode_protocol": "anthropic",
        },
    )
    cfg = client.get_opencode_config()
    assert cfg["protocol"] == "anthropic"
    assert cfg["endpoint_type"] == "messages"


def test_opencode_headers_are_stable_specific_and_secret_independent():
    first = client.opencode_headers("key-one")
    second = client.opencode_headers("key-two")
    assert first["x-opencode-session"]
    assert first["x-opencode-session"] == second["x-opencode-session"]
    assert "key-one" not in first["x-opencode-session"]
    assert first["User-Agent"].startswith("GalaxyHire/")
    assert "Mozilla" not in first["User-Agent"]
    assert second["Authorization"] == "Bearer key-two"


def test_structured_and_raw_runtime_dispatch_to_opencode(monkeypatch):
    monkeypatch.setattr(
        client,
        "_resolve",
        lambda step=None: ("opencode", "open-code-key", "muse-spark-1.3-contributor"),
    )
    structured = Result(answer="structured")
    monkeypatch.setattr(client, "_call_opencode_llm", lambda *args: structured)
    monkeypatch.setattr(client, "_call_opencode_raw", lambda *args: "raw")

    assert client._call_llm_once("system", "user", Result) is structured
    assert client._call_raw_once("system", "user") == "raw"


def test_opencode_responses_raw_sends_session_header_and_reasoning(monkeypatch):
    _settings(
        monkeypatch,
        {
            "opencode_base_url": "https://opencode.ai/zen/go/v1/responses",
            "opencode_endpoint_type": "responses",
            "opencode_reasoning_effort": "medium",
        },
    )
    sent = {}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, headers=None, json=None):
            sent.update(url=url, headers=headers, body=json)
            return FakeResponse(
                {"output": [{"type": "message", "content": [{"type": "output_text", "text": "pong"}]}]}
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    assert client._call_opencode_raw("test-key", "muse", "system", "user") == "pong"
    assert sent["url"] == "https://opencode.ai/zen/go/v1/responses"
    assert sent["headers"]["x-opencode-session"]
    assert sent["headers"]["User-Agent"].startswith("GalaxyHire/")
    assert sent["body"]["reasoning"] == {"effort": "medium"}


def test_opencode_messages_raw_sends_anthropic_headers_and_thinking(monkeypatch):
    _settings(
        monkeypatch,
        {
            "opencode_base_url": "https://opencode.ai/zen/go/v1",
            "opencode_endpoint_type": "messages",
            "opencode_protocol": "anthropic",
            "opencode_reasoning_effort": "high",
        },
    )
    sent = {}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, headers=None, json=None):
            sent.update(url=url, headers=headers, body=json)
            return FakeResponse({"content": [{"type": "text", "text": "hello"}]})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    assert client._call_opencode_raw("test-key", "claude-model", "system", "user") == "hello"
    assert sent["url"].endswith("/v1/messages")
    assert sent["headers"]["x-opencode-session"]
    assert sent["headers"]["x-api-key"] == "test-key"
    assert sent["headers"]["anthropic-version"] == "2023-06-01"
    assert sent["body"]["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert sent["body"]["max_tokens"] > 4096


def test_opencode_chat_raw_configures_sdk_headers_and_reasoning(monkeypatch):
    _settings(
        monkeypatch,
        {
            "opencode_base_url": "https://opencode.ai/zen/go/v1/chat/completions",
            "opencode_endpoint_type": "chat/completions",
            "opencode_reasoning_effort": "low",
        },
    )
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="chat reply"))])

    def fake_openai(**kwargs):
        captured["client"] = kwargs
        return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    monkeypatch.setattr(client, "_openai", fake_openai)
    assert client._call_opencode_raw("test-key", "chat-model", "system", "user") == "chat reply"
    assert captured["client"]["base_url"] == "https://opencode.ai/zen/go/v1"
    assert captured["client"]["default_headers"]["x-opencode-session"]
    assert captured["request"]["extra_body"] == {"reasoning_effort": "low"}


def test_opencode_structured_responses_fallback_keeps_required_headers(monkeypatch):
    _settings(
        monkeypatch,
        {
            "opencode_base_url": "https://opencode.ai/zen/go/v1",
            "opencode_endpoint_type": "responses",
            "opencode_reasoning_effort": "medium",
        },
    )
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, headers=None, json=None):
            calls.append((url, dict(headers), dict(json)))
            if len(calls) == 1:
                raise httpx.ConnectError("first format rejected")
            return FakeResponse(
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"answer":"ok"}'}],
                        }
                    ]
                }
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = client._call_opencode_llm("test-key", "muse", "system", "user", Result)
    assert result.answer == "ok"
    assert len(calls) == 2
    assert all(call[1]["x-opencode-session"] == calls[0][1]["x-opencode-session"] for call in calls)
    assert "text" in calls[0][2]
    assert "text" not in calls[1][2]
    assert calls[1][2]["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_opencode_probe_uses_headers_endpoint_and_reasoning(monkeypatch):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, headers=None, json=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse(
                {"output": [{"type": "message", "content": [{"type": "output_text", "text": "pong"}]}]}
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    result = await probe_provider_key(
        "opencode",
        "test-key",
        {
            "opencode_base_url": "https://opencode.ai/zen/go/v1/responses",
            "opencode_model": "muse-spark-1.3-contributor",
            "opencode_endpoint_type": "responses",
            "opencode_reasoning_effort": "medium",
        },
    )
    assert result["status"] == "ok"
    assert captured["url"].endswith("/v1/responses")
    assert captured["headers"]["x-opencode-session"]
    assert captured["body"]["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_opencode_model_listing_uses_same_session_header(monkeypatch):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, headers=None):
            captured.update(url=url, headers=headers)
            return FakeResponse({"data": [{"id": "model-b"}, {"id": "model-a"}]})

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    models = await list_provider_models(
        "opencode",
        "test-key",
        {"opencode_base_url": "https://opencode.ai/zen/go/v1/chat/completions"},
    )
    assert models == ["model-a", "model-b"]
    assert captured["url"] == "https://opencode.ai/zen/go/v1/models"
    assert captured["headers"]["x-opencode-session"]
    assert captured["headers"]["x-opencode-session"] == client.opencode_headers("other")["x-opencode-session"]


def test_custom_responses_do_not_receive_opencode_headers(monkeypatch):
    _settings(
        monkeypatch,
        {
            "custom_base_url": "https://custom.example/v1/responses",
            "custom_endpoint_type": "responses",
        },
    )
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, headers=None, json=None):
            captured["headers"] = headers
            return FakeResponse(
                {"output": [{"type": "message", "content": [{"type": "output_text", "text": "custom"}]}]}
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    assert client._call_custom_raw("custom-key", "custom-model", "system", "user") == "custom"
    assert "x-opencode-session" not in captured["headers"]
    assert captured["headers"]["User-Agent"] != client.opencode_headers("x")["User-Agent"]
