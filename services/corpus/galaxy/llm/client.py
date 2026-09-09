"""OpenAI-compatible LLM proxy client (docs/06 §4, docs/07 §4).

Single egress for every server-side LLM call. Bring-your-own endpoint — no provider hardcoded.
Two rules that are load-bearing for this setup:
  - `stream: false` is ALWAYS sent explicitly (omniroute streams by default otherwise).
  - a capability probe walks tool_choice request-shape variants until one is accepted, and the
    working variant is cached.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from galaxy.common.config import get_settings

log = structlog.get_logger(__name__)


# request-shape variants, tried in order (docs/06 §4)
TOOL_CHOICE_VARIANTS = [
    {"name": "named_tool_max_tokens", "token_key": "max_tokens", "tool_choice": "named"},
    {"name": "required_max_tokens", "token_key": "max_tokens", "tool_choice": "required"},
    {"name": "required_max_completion", "token_key": "max_completion_tokens", "tool_choice": "required"},
]


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str


def _config() -> LLMConfig:
    s = get_settings()
    return LLMConfig(base_url=s.llm_base_url.rstrip("/"), api_key=s.llm_api_key, model=s.llm_model)


class LLMClient:
    def __init__(self, config: LLMConfig | None = None, timeout: float = 120.0):
        self.config = config or _config()
        self.timeout = timeout

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> dict:
        """Non-streaming chat completion. `stream:false` always explicit."""
        body: dict[str, Any] = {
            "model": model or self.config.model or "default",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,  # ALWAYS explicit (docs/06 §4)
        }
        if tools:
            body["tools"] = tools
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.config.base_url}/chat/completions", json=body, headers=headers
            )
            resp.raise_for_status()
            return resp.json()

    async def probe(self) -> dict | None:
        """Find a working tool-call request shape; cache and return it (docs/06 §4)."""
        tool = {
            "type": "function",
            "function": {
                "name": "noop",
                "description": "probe",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            for variant in TOOL_CHOICE_VARIANTS:
                body: dict[str, Any] = {
                    "model": self.config.model or "default",
                    "messages": [{"role": "user", "content": "say hi"}],
                    "tools": [tool],
                    "stream": False,
                    variant["token_key"]: 16,
                }
                body["tool_choice"] = (
                    {"type": "function", "function": {"name": "noop"}}
                    if variant["tool_choice"] == "named"
                    else "required"
                )
                try:
                    resp = await client.post(
                        f"{self.config.base_url}/chat/completions", json=body, headers=headers
                    )
                    if resp.status_code < 400:
                        log.info("llm.probe_ok", variant=variant["name"])
                        return variant
                except httpx.HTTPError as exc:
                    log.warning("llm.probe_error", variant=variant["name"], error=str(exc))
        log.warning("llm.probe_failed")
        return None
