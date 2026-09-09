from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from llm import client
from llm.multimodal import (
    CapabilityState,
    MediaImage,
    PdfMedia,
    capability_for,
    content_parts,
    media_rejection,
)


class Result(BaseModel):
    answer: str


def _media() -> PdfMedia:
    # A tiny, bounded placeholder is sufficient to inspect request shape.
    return PdfMedia((MediaImage("YWJj", "image/png", 1, 1),), 1, 1)


def test_capabilities_are_explicit_and_conservative():
    assert capability_for("openai", "gpt-4o", "chat/completions").image_input is CapabilityState.SUPPORTED
    assert capability_for("anthropic", "claude-sonnet-4-6", "messages").image_input is CapabilityState.SUPPORTED
    assert capability_for("custom", "gpt-4o", "responses").image_input is CapabilityState.UNKNOWN
    assert capability_for("opencode", "muse-spark-1.3-contributor", "responses").image_input is CapabilityState.UNKNOWN
    assert capability_for("openai", "gpt-4", "chat/completions").image_input is CapabilityState.UNSUPPORTED


def test_request_shapes_for_responses_chat_and_anthropic():
    media = _media()
    responses = content_parts("responses", "resume text", media)
    assert responses[0] == {"type": "input_text", "text": "resume text"}
    assert responses[1]["type"] == "input_image"
    assert responses[1]["image_url"].startswith("data:image/png;base64,")

    chat = content_parts("chat/completions", "resume text", media)
    assert chat[0] == {"type": "text", "text": "resume text"}
    assert chat[1]["type"] == "image_url"
    assert chat[1]["image_url"]["url"].startswith("data:image/png;base64,")

    anthropic = content_parts("messages", "resume text", media)
    assert anthropic[0] == {"type": "text", "text": "resume text"}
    assert anthropic[1]["type"] == "image"
    assert anthropic[1]["source"] == {"type": "base64", "media_type": "image/png", "data": "YWJj"}


def test_media_rejection_excludes_auth_not_found_and_rate_limit():
    class Error(Exception):
        def __init__(self, status_code):
            self.status_code = status_code

    assert media_rejection(Error(400))
    assert not media_rejection(Error(401))
    assert not media_rejection(Error(404))
    assert not media_rejection(Error(429))


def test_opencode_responses_media_fallback_retries_once_without_media(monkeypatch):
    media = _media()
    monkeypatch.setattr(client, "_resolve", lambda step=None: ("opencode", "k", "muse-spark-1.3-contributor"))
    monkeypatch.setattr(client, "get_opencode_config", lambda: {
        "url": "https://opencode.ai/zen/go/v1/responses",
        "protocol": "openai",
        "endpoint_type": "responses",
        "reasoning": "",
    })
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            if len(calls) == 1:
                raise Error400()

        def json(self):
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"answer":"ok"}'}]}]}

    class Error400(Exception):
        status_code = 400

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, headers=None, json=None):
            calls.append(json)
            return FakeResponse()

    monkeypatch.setattr(client.httpx, "Client", FakeClient)
    result = client.call_llm("system", "resume text", Result, media=media)
    assert result.answer == "ok"
    assert len(calls) == 2
    assert calls[0]["input"][0]["content"][1]["type"] == "input_image"
    assert calls[1]["input"][0]["content"] == "resume text"


def test_openai_chat_media_fallback_retries_once_without_media(monkeypatch):
    media = _media()
    monkeypatch.setattr(client, "_resolve", lambda step=None: ("openai", "k", "gpt-4o"))
    calls = []

    class Error400(Exception):
        status_code = 400

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise Error400()
            return Result(answer="ok")

    class FakeCompat:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(client, "_openai", lambda **kwargs: object())
    monkeypatch.setattr(client.instructor, "from_openai", lambda *args, **kwargs: FakeCompat())
    result = client.call_llm("system", "resume text", Result, media=media)
    assert result.answer == "ok"
    assert calls[0]["messages"][1]["content"][1]["type"] == "image_url"
    assert calls[1]["messages"][1]["content"] == "resume text"


def test_anthropic_media_fallback_retries_once_without_media(monkeypatch):
    media = _media()
    monkeypatch.setattr(client, "_resolve", lambda step=None: ("anthropic", "k", "claude-sonnet-4-6"))
    calls = []

    class Error422(Exception):
        status_code = 422

    class FakeMessages:
        def parse(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise Error422()
            return SimpleNamespace(parsed_output=Result(answer="ok"))

    class FakeAnthropic:
        messages = FakeMessages()

    monkeypatch.setattr(client, "_anthropic", lambda **kwargs: FakeAnthropic())
    result = client.call_llm("system", "resume text", Result, media=media)
    assert result.answer == "ok"
    assert calls[0]["messages"][0]["content"][1]["type"] == "image"
    assert calls[1]["messages"][0]["content"] == "resume text"


def test_no_media_keeps_legacy_text_content_shape():
    assert content_parts("responses", "plain", None) == "plain"
    assert content_parts("chat/completions", "plain", None) == "plain"
    assert content_parts("messages", "plain", None) == "plain"


def test_profile_run_attaches_media_only_for_verified_model(monkeypatch):
    from profile import ingestor

    captured = {}
    monkeypatch.setattr(ingestor, "_document", lambda path: "Jane Doe\nBuilt systems")
    monkeypatch.setattr(ingestor, "preprocess_resume_text", lambda text: text)
    monkeypatch.setattr(ingestor, "get_existing_profile_context", lambda: {})
    monkeypatch.setattr(client, "resolve_config", lambda step=None: ("openai", "k", "gpt-4o"))
    monkeypatch.setattr("llm.resolve_config", lambda step=None: ("openai", "k", "gpt-4o"))
    monkeypatch.setattr("llm.provider_needs_key", lambda provider: True)
    monkeypatch.setattr("llm.multimodal.render_pdf_media", lambda path: _media())

    def fake_call(system, user, model, step=None, **kwargs):
        captured.update(kwargs)
        return Result(answer="ignored") if model is Result else model()

    # Avoid coupling the test to the large CandidateProfile prompt.
    monkeypatch.setattr("llm.call_llm", fake_call)
    ingestor.run(pdf="resume.pdf", existing_profile={})
    assert captured["media"].pages_rendered == 1
