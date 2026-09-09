from __future__ import annotations

import time

from fastapi import APIRouter

from api.rate_limit import RateLimiter, require_rate_limit


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["ai"])
    check_limiter = RateLimiter(6, 60)

    @router.post("/ai/check")
    async def check_ai() -> dict:
        """One-round-trip connectivity test against the configured LLM provider.

        Resolves the exact same config the generator/ingestor use, reports what
        is set, then sends a minimal structured ping so 'is my key working' is
        answerable from the UI instead of being discovered as a silent empty
        profile after a resume upload."""
        import asyncio

        require_rate_limit(check_limiter)
        from llm.client import _resolve, provider_needs_key

        provider, key, model = _resolve(None)
        key_present = bool(key)
        result: dict = {
            "provider": provider,
            "model": model,
            "key_present": key_present,
            "needs_key": provider_needs_key(provider),
            "ok": False,
            "error": "",
            "latency_ms": 0,
        }
        if provider_needs_key(provider) and not key:
            result["error"] = (
                f"No API key configured for provider {provider!r}. "
                "Add your key in Settings."
            )
            return result

        from pydantic import BaseModel

        class _Ping(BaseModel):
            ok: bool

        started = time.monotonic()
        try:
            from llm import call_llm

            parsed = await asyncio.to_thread(
                call_llm,
                'You are a health probe. Reply with {"ok": true} and nothing else.',
                "ping",
                _Ping,
                None,
            )
            result["latency_ms"] = int((time.monotonic() - started) * 1000)
            result["ok"] = bool(getattr(parsed, "ok", False))
            if not result["ok"]:
                result["error"] = "Provider replied but not with the expected shape."
        except Exception as exc:
            result["latency_ms"] = int((time.monotonic() - started) * 1000)
            # First line only — SDK tracebacks are walls of text in a pill.
            result["error"] = str(exc).splitlines()[0][:400]
            # OpenRouter treats malformed tokens as *absent* auth ("Missing
            # Authentication header") instead of "invalid key" — tell the user.
            try:
                from llm.client import _provider_base_url

                base = _provider_base_url(provider) if provider != "ollama" else ""
            except Exception:
                base = ""
            if "openrouter.ai" in base and key_present and not key.startswith("sk-or"):
                result["error"] += (
                    " — this doesn't look like an OpenRouter key (real keys start "
                    "with 'sk-or-v1-…'); create one at https://openrouter.ai/keys"
                )
        return result

    return router
