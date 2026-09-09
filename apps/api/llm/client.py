import asyncio
import concurrent.futures
import json
import logging
import os
import re
import threading
import time
import uuid
from urllib.parse import urlparse
import httpx
import anthropic
import instructor
import openai
from openai import OpenAI
from pydantic import BaseModel
from data.repository import Repository, create_repository
from core.logging import get_logger
from llm.multimodal import PdfMedia, content_parts, media_rejection

_log = get_logger(__name__)

# 120s — a single LLM call taking longer than this is hung, not slow. (H5)
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

# OpenCode Go uses this value to keep a conversation on the same backend and to
# reuse its prompt cache.  One process-lifetime id is deliberately shared by
# validation, model discovery and runtime calls; it is never derived from (and
# therefore can never reveal) an API key.
_OPENCODE_DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
_OPENCODE_USER_AGENT = "GalaxyHire/1.4.0"
_OPENCODE_SESSION = f"galaxyhire-{uuid.uuid4().hex}"


def opencode_headers(key: str, *, anthropic_protocol: bool = False) -> dict[str, str]:
    """Headers required on every OpenCode Go request path."""
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": _OPENCODE_USER_AGENT,
        "x-opencode-session": _OPENCODE_SESSION,
    }
    if anthropic_protocol:
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    return headers

# Number of retries (after the first attempt) for transient LLM errors. (C2)
_MAX_LLM_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds; doubles each retry → 1s, 2s, 4s

# H5: a dedicated, bounded thread pool for blocking LLM calls. Using the default
# asyncio executor meant a burst of slow (up to 120s) generations could occupy
# every default worker and starve all other to_thread work (DB reads, file IO).
# Keeping LLM calls on their own pool caps the blast radius at max_workers.
LLM_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")

# Cache constructed SDK clients so each LLM call reuses the underlying httpx
# connection pool / TLS session instead of paying full client + TLS setup every
# call. Keyed by the exact constructor kwargs (via repr, since httpx.Timeout is
# not hashable) so different base_url/key/timeout/max_retries configs get
# distinct clients. OpenAI/Anthropic clients are documented thread-safe, so
# sharing one across the bounded LLM pool is safe.
_CLIENT_CACHE: dict = {}
_CLIENT_CACHE_LOCK = threading.Lock()


def _cached_raw_client(kind: str, factory, **kwargs):
    key = (kind, tuple(sorted((name, repr(val)) for name, val in kwargs.items())))
    client = _CLIENT_CACHE.get(key)
    if client is not None:
        return client
    with _CLIENT_CACHE_LOCK:
        client = _CLIENT_CACHE.get(key)
        if client is None:
            client = factory(**kwargs)
            _CLIENT_CACHE[key] = client
        return client


def _openai(**kwargs) -> OpenAI:
    """Cached raw OpenAI client (shared httpx pool). Wrap with instructor per
    call where structured output is needed — instructor.from_openai does not
    mutate the passed client, so re-wrapping a cached client is safe."""
    return _cached_raw_client("openai", OpenAI, **kwargs)


def _anthropic(**kwargs):
    """Cached raw Anthropic client (shared httpx pool)."""
    return _cached_raw_client("anthropic", anthropic.Anthropic, **kwargs)


def reset_client_cache() -> None:
    """Drop all cached SDK clients (used by tests / after a key change)."""
    with _CLIENT_CACHE_LOCK:
        _CLIENT_CACHE.clear()


async def acall_llm(
    s: str,
    u: str,
    m: type[BaseModel],
    step: str | None = None,
    media: PdfMedia | None = None,
):
    """Async wrapper that runs call_llm on the dedicated LLM thread pool (H5)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(LLM_EXECUTOR, lambda: call_llm(s, u, m, step, media=media))


async def acall_raw(s: str, u: str, step: str | None = None, media: PdfMedia | None = None) -> str:
    """Async wrapper that runs call_raw on the dedicated LLM thread pool (H5)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(LLM_EXECUTOR, lambda: call_raw(s, u, step, media=media))


def _is_retryable_llm_error(exc: Exception) -> bool:
    """True for transient errors worth retrying (rate limit, connection, 5xx).

    Permanent errors — authentication, invalid request, 4xx other than 429 —
    return False so they propagate immediately rather than wasting retries.
    """
    if isinstance(
        exc,
        (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            anthropic.RateLimitError,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
        ),
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or 500 <= exc.response.status_code < 600
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    # HTTP 5xx from either SDK (APIStatusError subclasses expose status_code).
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status == 429 or 500 <= status < 600)


def is_transient_llm_error(exc: Exception) -> bool:
    """Public alias of the transient-error classifier (used by callers that
    need to distinguish retryable LLM failures from permanent ones, e.g. M4)."""
    return _is_retryable_llm_error(exc)


def _safe_error_label(exc: Exception) -> str:
    """Return diagnostics without echoing provider bodies or prompt data."""

    status = getattr(exc, "status_code", None)
    if not isinstance(status, int) and getattr(exc, "response", None) is not None:
        status = getattr(exc.response, "status_code", None)
    return f"{type(exc).__name__}(status={status})" if isinstance(status, int) else type(exc).__name__


def _retry_llm_call(fn, *, max_retries: int = _MAX_LLM_RETRIES):
    """Run ``fn`` with exponential backoff on transient LLM errors.

    Retries up to ``max_retries`` times with 1s/2s/4s delays. Permanent errors
    are re-raised on the first occurrence.
    """
    delay = _RETRY_BASE_DELAY
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= max_retries or not _is_retryable_llm_error(exc):
                raise
            _log.warning(
                "transient LLM error (attempt %d/%d) — retrying in %.0fs: %s",
                attempt + 1,
                max_retries,
                delay,
                _safe_error_label(exc),
            )
            time.sleep(delay)
            delay *= 2
# STABILITY: thread-safe LLM repository singleton
_repo_lock = threading.RLock()
_repo: Repository = create_repository()


def configure_repository(repo: Repository) -> None:
    global _repo
    with _repo_lock:
        _repo = repo


def get_repository() -> Repository:
    with _repo_lock:
        return _repo


def get_setting(key: str, default: str = "") -> str:
    return get_repository().settings.get_setting(key, default)

# Maps provider id → settings key holding the global API key
_KEY_NAMES: dict[str, str] = {
    "anthropic": "anthropic_key",
    "gemini":    "gemini_api_key",
    "groq":      "groq_api_key",
    "nvidia":    "nvidia_api_key",
    "openai":    "openai_api_key",
    "deepseek":  "deepseek_api_key",
    "xai":       "xai_api_key",
    "kimi":      "kimi_api_key",
    "mistral":   "mistral_api_key",
    "openrouter": "openrouter_api_key",
    "together":  "together_api_key",
    "fireworks": "fireworks_api_key",
    "cerebras":  "cerebras_api_key",
    "perplexity": "perplexity_api_key",
    "huggingface": "huggingface_api_key",
    "cohere": "cohere_api_key",
    "sambanova": "sambanova_api_key",
    "qwen": "qwen_api_key",
    "azure": "azure_openai_api_key",
    "opencode": "opencode_api_key",
    "custom":    "custom_api_key",
}

# Maps provider id → environment variable fallback
_ENV_NAMES: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini":    "GEMINI_API_KEY",
    "groq":      "GROQ_API_KEY",
    "nvidia":    "NVIDIA_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "deepseek":  "DEEPSEEK_API_KEY",
    "xai":       "XAI_API_KEY",
    "kimi":      "MOONSHOT_API_KEY",
    "mistral":   "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "together":  "TOGETHER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "cerebras":  "CEREBRAS_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "huggingface": "HF_TOKEN",
    "cohere": "COHERE_API_KEY",
    "sambanova": "SAMBANOVA_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "custom":    "OPENAI_COMPAT_API_KEY",
}

# Default model per provider (used when no step/global model is set)
_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "gemini":    "gemini-2.5-flash",
    "groq":      "llama-3.3-70b-versatile",
    "nvidia":    "z-ai/glm-5.1",
    "openai":    "gpt-4o-mini",
    "deepseek":  "deepseek-chat",
    "xai":       "grok-4",
    "kimi":      "kimi-k2.6",
    "mistral":   "mistral-large-latest",
    "openrouter": "openrouter/auto",
    "together":  "openai/gpt-oss-120b",
    "fireworks": "accounts/fireworks/models/llama-v3p1-70b-instruct",
    "cerebras":  "llama-3.3-70b",
    "perplexity": "sonar",
    "huggingface": "openai/gpt-oss-120b",
    "cohere": "command-a-03-2025",
    "sambanova": "Meta-Llama-3.3-70B-Instruct",
    "qwen": "qwen-plus",
    "azure": "gpt-4o-mini",
    "opencode": "muse-spark-1.3-contributor",
    "custom":    "model-id",
    "ollama":    "llama3",
    "claude_cli": "claude-sonnet-4-6",  # uses the user's Claude subscription via the claude CLI (no API key)
    "codex_cli":  "gpt-5.5",             # ChatGPT-account Codex only allows its own default model (gpt-5.5 as of 2026-06); codex falls back to the account default if this is unavailable
    "gemini_cli": "",                    # uses the user's Google account / Gemini plan via the gemini CLI; "" = the CLI's own default model
    "copilot_cli": "",                   # uses the user's GitHub Copilot subscription via the copilot CLI; "" = the CLI's own default model
}

_OPENAI_COMPAT_BASE_URLS: dict[str, str] = {
    "xai": "https://api.x.ai/v1",
    "kimi": "https://api.moonshot.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "perplexity": "https://api.perplexity.ai",
    "huggingface": "https://router.huggingface.co/v1",
    "cohere": "https://api.cohere.ai/compatibility/v1",
    "sambanova": "https://api.sambanova.ai/v1",
    "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}

_OPENAI_COMPAT_PROVIDERS = set(_OPENAI_COMPAT_BASE_URLS) | {"azure", "opencode", "custom"}
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}

# Providers that authenticate WITHOUT an API key: ollama (local) and the
# subscription CLIs (claude_cli / codex_cli, which use the user's own CLI login).
# Centralized so every "needs a key?" check stays in sync.
# Subscription-CLI providers: shell out to a coding-assistant CLI the user has
# already signed into with their OWN plan (no API key). Add new ones here AND in
# llm/subscription_cli.py (_EXE + complete_text branch + status/login).
SUBSCRIPTION_CLI_PROVIDERS = frozenset({"claude_cli", "codex_cli", "gemini_cli", "copilot_cli"})
KEYLESS_PROVIDERS = frozenset({"ollama"}) | SUBSCRIPTION_CLI_PROVIDERS


def provider_needs_key(provider: str) -> bool:
    """True if the provider requires an API key (i.e. not ollama or a subscription CLI)."""
    return provider not in KEYLESS_PROVIDERS


def _validate_base_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid base URL: {url}")
    host = parsed.hostname or ""
    if host.lower() in _BLOCKED_HOSTS:
        raise ValueError(f"Cannot use localhost as LLM base URL: {url}")
    # Resolve hostnames too, not just literal IPs: a name like "localtest.me"
    # resolves to 127.0.0.1 and a bare metadata host would otherwise slip past
    # the literal-IP check below. is_public_host enforces the same
    # no-private/loopback/link-local policy after DNS resolution.
    from core.url_guard import is_public_host

    if not is_public_host(host):
        raise ValueError(f"Cannot use private/loopback/internal host as LLM base URL: {url}")
    return url


def _provider_base_url(provider: str) -> str:
    if provider == "azure":
        base = (
            get_setting("azure_openai_endpoint", "")
            or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        ).strip().rstrip("/")
        if not base:
            raise ValueError("Azure OpenAI endpoint is required")
        if not base.endswith("/openai/v1"):
            base = f"{base}/openai/v1"
        return _validate_base_url(base)
    if provider == "custom":
        raw = (
            get_setting("custom_base_url", "")
            or os.environ.get("OPENAI_COMPAT_BASE_URL", "")
            or "https://api.openai.com/v1"
        ).strip().rstrip("/")
        for suffix in ("/chat/completions", "/responses", "/messages"):
            if raw.endswith(suffix):
                raw = raw[:-len(suffix)].rstrip("/")
                break
        return _validate_base_url(raw)
    if provider == "opencode":
        raw = (
            get_setting("opencode_base_url", "")
            or os.environ.get("OPENCODE_BASE_URL", "")
            or _OPENCODE_DEFAULT_BASE_URL
        ).strip().rstrip("/")
        for suffix in ("/chat/completions", "/responses", "/messages"):
            if raw.endswith(suffix):
                raw = raw[:-len(suffix)].rstrip("/")
                break
        return _validate_base_url(raw)
    return _OPENAI_COMPAT_BASE_URLS[provider]


def get_custom_config() -> dict[str, str]:
    """Retrieve full custom endpoint configuration: url, protocol, format, reasoning."""
    url = (
        get_setting("custom_base_url", "")
        or os.environ.get("OPENAI_COMPAT_BASE_URL", "")
        or "https://api.openai.com/v1"
    ).strip()
    protocol = get_setting("custom_protocol", "").strip().lower()
    endpoint_type = get_setting("custom_endpoint_type", "").strip().lower()
    reasoning = get_setting("custom_reasoning_effort", "").strip().lower()

    # Auto-detect format & protocol from url if not explicitly chosen
    url_lower = url.lower()
    if not endpoint_type:
        if "/responses" in url_lower:
            endpoint_type = "responses"
        elif "/messages" in url_lower:
            endpoint_type = "messages"
        elif "/chat/completions" in url_lower:
            endpoint_type = "chat/completions"
        else:
            endpoint_type = "chat/completions"

    if not protocol:
        protocol = "anthropic" if endpoint_type == "messages" or "/anthropic" in url_lower else "openai"

    return {
        "url": url,
        "protocol": protocol,
        "endpoint_type": endpoint_type,
        "reasoning": reasoning,
    }


def get_opencode_config() -> dict[str, str]:
    """Return the isolated OpenCode Go endpoint configuration.

    OpenCode Go exposes OpenAI Responses, OpenAI chat-completions, and
    Anthropic messages endpoints.  A full endpoint URL is accepted as well as
    the base ``/v1`` URL; an explicit format always wins over URL detection.
    """
    url = (
        get_setting("opencode_base_url", "")
        or os.environ.get("OPENCODE_BASE_URL", "")
        or _OPENCODE_DEFAULT_BASE_URL
    ).strip()
    endpoint_type = get_setting("opencode_endpoint_type", "").strip().lower()
    protocol = get_setting("opencode_protocol", "").strip().lower()
    reasoning = get_setting("opencode_reasoning_effort", "").strip().lower()

    if not endpoint_type:
        url_lower = url.lower().rstrip("/")
        if url_lower.endswith("/chat/completions"):
            endpoint_type = "chat/completions"
        elif url_lower.endswith("/messages") or protocol == "anthropic":
            endpoint_type = "messages"
        else:
            # OpenCode Go's contributor models, including muse-spark, use the
            # Responses API, so this is the useful safe default for a base URL.
            endpoint_type = "responses"
    if not protocol:
        protocol = "anthropic" if endpoint_type == "messages" else "openai"

    return {
        "url": url,
        "protocol": protocol,
        "endpoint_type": endpoint_type,
        "reasoning": reasoning,
    }


def resolve_custom_endpoints(url: str, endpoint_type: str) -> tuple[str, str]:
    """Returns (endpoint_url, base_url).

    endpoint_url is the exact full URL to POST to (e.g. https://opencode.ai/zen/go/v1/responses).
    base_url is the root /v1 path (without /chat/completions, /responses, /messages).
    """
    clean_url = url.strip().rstrip("/")
    base = clean_url
    for suffix in ("/chat/completions", "/responses", "/messages"):
        if base.endswith(suffix):
            base = base[:-len(suffix)].rstrip("/")
            break

    if endpoint_type == "chat/completions":
        target = f"{base}/chat/completions"
    elif endpoint_type == "responses":
        target = f"{base}/responses"
    elif endpoint_type == "messages":
        target = f"{base}/messages"
    else:
        target = f"{base}/chat/completions"

    return target, base


def _extract_and_validate_json(text: str, m: type[BaseModel]) -> BaseModel:
    clean = (text or "").strip()
    if "```" in clean:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean)
        if match:
            clean = match.group(1).strip()
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean)
    if match:
        clean = match.group(1).strip()
    return m.model_validate_json(clean)


def _call_custom_raw(
    key: str,
    model: str,
    s: str,
    u: str,
    media: PdfMedia | None = None,
) -> str:
    cfg = get_custom_config()
    target_url, base_url = resolve_custom_endpoints(cfg["url"], cfg["endpoint_type"])
    reasoning = cfg.get("reasoning", "")
    endpoint_type = cfg.get("endpoint_type", "chat/completions")

    if endpoint_type == "responses":
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": _BROWSER_UA,
        }
        body: dict = {
            "model": model,
            "input": [{"role": "user", "content": content_parts("responses", u, media)}],
        }
        if s:
            body["instructions"] = s
        if reasoning in ("low", "medium", "high"):
            body["reasoning"] = {"effort": reasoning}

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(target_url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            text_parts = []
            for item in data.get("output", []):
                if item.get("type") == "message":
                    for c in item.get("content", []):
                        if c.get("type") in ("output_text", "text") and c.get("text"):
                            text_parts.append(c.get("text", ""))
            return "".join(text_parts).strip()

    elif endpoint_type == "messages":
        headers = {
            "x-api-key": key,
            "Authorization": f"Bearer {key}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "User-Agent": _BROWSER_UA,
        }
        body = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": content_parts("messages", u, media)}],
        }
        if s:
            body["system"] = s
        if reasoning in ("low", "medium", "high"):
            budget = 1024 if reasoning == "low" else 2048 if reasoning == "medium" else 4096
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(target_url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            text_parts = []
            for item in data.get("content", []):
                if item.get("type") == "text" and item.get("text"):
                    text_parts.append(item.get("text", ""))
            return "".join(text_parts).strip()

    else:  # chat/completions
        openai_client = _openai(
            base_url=base_url,
            api_key=key,
            timeout=_TIMEOUT,
            max_retries=0,
            default_headers={"User-Agent": _BROWSER_UA},
        )
        kwargs: dict = {
            "model": model,
            "messages": (
                [{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}]
                if s
                else [{"role": "user", "content": content_parts("chat/completions", u, media)}]
            ),
        }
        if reasoning in ("low", "medium", "high"):
            kwargs["extra_body"] = {"reasoning_effort": reasoning}
        resp = openai_client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""


def _call_custom_llm(
    key: str,
    model: str,
    s: str,
    u: str,
    m: type[BaseModel],
    media: PdfMedia | None = None,
) -> BaseModel:
    cfg = get_custom_config()
    target_url, base_url = resolve_custom_endpoints(cfg["url"], cfg["endpoint_type"])
    reasoning = cfg.get("reasoning", "")
    endpoint_type = cfg.get("endpoint_type", "chat/completions")

    if endpoint_type == "responses":
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": _BROWSER_UA,
        }
        schema = m.model_json_schema()
        instructions = (s + "\nYou must output strictly conforming to the JSON schema: " + json.dumps(schema)) if s else ("Output valid JSON conforming to schema: " + json.dumps(schema))
        body: dict = {
            "model": model,
            "instructions": instructions,
            "input": [{"role": "user", "content": content_parts("responses", u, media)}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": getattr(m, "__name__", "StructuredOutput"),
                    "schema": schema,
                }
            },
        }
        if reasoning in ("low", "medium", "high"):
            body["reasoning"] = {"effort": reasoning}

        with httpx.Client(timeout=_TIMEOUT) as client:
            try:
                resp = client.post(target_url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                if media and media_rejection(exc):
                    raise
                body.pop("text", None)
                resp = client.post(target_url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()

            text_parts = []
            for item in data.get("output", []):
                if item.get("type") == "message":
                    for c in item.get("content", []):
                        if c.get("type") in ("output_text", "text") and c.get("text"):
                            text_parts.append(c.get("text", ""))
            raw = "".join(text_parts).strip()
            return _extract_and_validate_json(raw, m)

    elif endpoint_type == "messages":
        schema = m.model_json_schema()
        system_prompt = (s + "\nYou must return ONLY valid JSON matching this schema:\n" + json.dumps(schema)) if s else ("Return ONLY valid JSON matching this schema:\n" + json.dumps(schema))
        raw = _call_custom_raw(key, model, system_prompt, u, media=media)
        return _extract_and_validate_json(raw, m)

    else:  # chat/completions
        openai_client = _openai(
            base_url=base_url,
            api_key=key,
            timeout=_TIMEOUT,
            max_retries=0,
            default_headers={"User-Agent": _BROWSER_UA},
        )
        compat_client = instructor.from_openai(openai_client, mode=instructor.Mode.JSON)
        kwargs: dict = {
            "model": model,
            "response_model": m,
            "max_retries": 0 if media else 1,
            "messages": (
                [{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}]
                if s
                else [{"role": "user", "content": content_parts("chat/completions", u, media)}]
            ),
        }
        if reasoning in ("low", "medium", "high"):
            kwargs["extra_body"] = {"reasoning_effort": reasoning}
        try:
            return compat_client.chat.completions.create(**kwargs)
        except Exception as exc:
            if media and media_rejection(exc):
                raise
            schema = m.model_json_schema()
            system_prompt = (s + "\nReturn ONLY valid JSON matching this schema:\n" + json.dumps(schema)) if s else ("Return ONLY valid JSON matching this schema:\n" + json.dumps(schema))
            raw = _call_custom_raw(key, model, system_prompt, u, media=media)
            return _extract_and_validate_json(raw, m)


def _call_opencode_raw(
    key: str,
    model: str,
    s: str,
    u: str,
    media: PdfMedia | None = None,
) -> str:
    """Call the configured OpenCode Go endpoint for free-form text."""
    cfg = get_opencode_config()
    target_url, base_url = resolve_custom_endpoints(cfg["url"], cfg["endpoint_type"])
    reasoning = cfg.get("reasoning", "")
    endpoint_type = cfg["endpoint_type"]

    if endpoint_type == "responses":
        body: dict = {
            "model": model,
            "input": [{"role": "user", "content": content_parts("responses", u, media)}],
        }
        if s:
            body["instructions"] = s
        if reasoning in ("low", "medium", "high"):
            body["reasoning"] = {"effort": reasoning}
        with httpx.Client(timeout=_TIMEOUT) as client:
            response = client.post(target_url, headers=opencode_headers(key), json=body)
            response.raise_for_status()
            data = response.json()
        parts: list[str] = []
        for item in data.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") in ("output_text", "text") and content.get("text"):
                        parts.append(content["text"])
        return "".join(parts).strip()

    if endpoint_type == "messages":
        body = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": content_parts("messages", u, media)}],
        }
        if s:
            body["system"] = s
        if reasoning in ("low", "medium", "high"):
            budget = {"low": 1024, "medium": 2048, "high": 4096}[reasoning]
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            body["max_tokens"] = budget + 4096
        with httpx.Client(timeout=_TIMEOUT) as client:
            response = client.post(
                target_url,
                headers=opencode_headers(key, anthropic_protocol=True),
                json=body,
            )
            response.raise_for_status()
            data = response.json()
        return "".join(
            str(item.get("text") or "")
            for item in data.get("content", [])
            if item.get("type") == "text"
        ).strip()

    # chat/completions
    openai_client = _openai(
        base_url=base_url,
        api_key=key,
        timeout=_TIMEOUT,
        max_retries=0,
        default_headers=opencode_headers(key),
    )
    kwargs: dict = {
        "model": model,
        "messages": (
            [{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}]
            if s
            else [{"role": "user", "content": content_parts("chat/completions", u, media)}]
        ),
    }
    if reasoning in ("low", "medium", "high"):
        kwargs["extra_body"] = {"reasoning_effort": reasoning}
    response = openai_client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


def _call_opencode_llm(
    key: str,
    model: str,
    s: str,
    u: str,
    m: type[BaseModel],
    media: PdfMedia | None = None,
) -> BaseModel:
    """Call the configured OpenCode Go endpoint for structured output."""
    cfg = get_opencode_config()
    target_url, base_url = resolve_custom_endpoints(cfg["url"], cfg["endpoint_type"])
    reasoning = cfg.get("reasoning", "")
    endpoint_type = cfg["endpoint_type"]

    if endpoint_type == "responses":
        schema = m.model_json_schema()
        instructions = (
            s + "\nYou must output strictly conforming to the JSON schema: " + json.dumps(schema)
            if s
            else "Output valid JSON conforming to schema: " + json.dumps(schema)
        )
        body: dict = {
            "model": model,
            "instructions": instructions,
            "input": [{"role": "user", "content": content_parts("responses", u, media)}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": getattr(m, "__name__", "StructuredOutput"),
                    "schema": schema,
                }
            },
        }
        if reasoning in ("low", "medium", "high"):
            body["reasoning"] = {"effort": reasoning}

        headers = opencode_headers(key)
        with httpx.Client(timeout=_TIMEOUT) as client:
            try:
                response = client.post(target_url, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                if media and media_rejection(exc):
                    raise
                # Some compatible Responses implementations do not support
                # json_schema.  The schema remains in the instructions.
                body.pop("text", None)
                response = client.post(target_url, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
        parts: list[str] = []
        for item in data.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") in ("output_text", "text") and content.get("text"):
                        parts.append(content["text"])
        return _extract_and_validate_json("".join(parts).strip(), m)

    if endpoint_type == "messages":
        schema = m.model_json_schema()
        system_prompt = (
            s + "\nYou must return ONLY valid JSON matching this schema:\n" + json.dumps(schema)
            if s
            else "Return ONLY valid JSON matching this schema:\n" + json.dumps(schema)
        )
        raw = _call_opencode_raw(key, model, system_prompt, u, media=media)
        return _extract_and_validate_json(raw, m)

    # chat/completions
    openai_client = _openai(
        base_url=base_url,
        api_key=key,
        timeout=_TIMEOUT,
        max_retries=0,
        default_headers=opencode_headers(key),
    )
    compat_client = instructor.from_openai(openai_client, mode=instructor.Mode.JSON)
    kwargs: dict = {
        "model": model,
        "response_model": m,
        "max_retries": 0 if media else 1,
        "messages": (
            [{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}]
            if s
            else [{"role": "user", "content": content_parts("chat/completions", u, media)}]
        ),
    }
    if reasoning in ("low", "medium", "high"):
        kwargs["extra_body"] = {"reasoning_effort": reasoning}
    try:
        return compat_client.chat.completions.create(**kwargs)
    except Exception as exc:
        if media and media_rejection(exc):
            raise
        schema = m.model_json_schema()
        system_prompt = (
            s + "\nReturn ONLY valid JSON matching this schema:\n" + json.dumps(schema)
            if s
            else "Return ONLY valid JSON matching this schema:\n" + json.dumps(schema)
        )
        raw = _call_opencode_raw(key, model, system_prompt, u, media=media)
        return _extract_and_validate_json(raw, m)



def _resolve(step: str | None = None) -> tuple[str, str, str]:
    """
    Resolve (provider, api_key, model) for a given pipeline step.

    Priority order:
      1. Step-specific setting  ({step}_provider / {step}_api_key / {step}_model)
      2. Global setting         (llm_provider / provider key / nvidia_model etc.)
      3. Environment variable   (ANTHROPIC_API_KEY etc.)
      4. Hardcoded defaults
    """
    sp = get_setting(f"{step}_provider", "") if step else ""
    sk = get_setting(f"{step}_api_key",  "") if step else ""
    sm = get_setting(f"{step}_model",    "") if step else ""

    p = sp or get_setting("llm_provider", "ollama")

    # API key: step-specific > global setting for this provider > env var
    if sk:
        k = sk
    else:
        k = (get_setting(_KEY_NAMES.get(p, ""), "")
             or os.environ.get(_ENV_NAMES.get(p, ""), "")
             or (os.environ.get("GOOGLE_API_KEY", "") if p == "gemini" else ""))

    # Model: step-specific > provider-level setting > default
    if sm:
        model = sm
    elif p in _DEFAULT_MODELS:
        model = get_setting(f"{p}_model", _DEFAULT_MODELS[p])
    else:
        model = _DEFAULT_MODELS.get(p, "llama3")

    if step:
        _log.debug("step=%s → provider=%s model=%s", step, p, model)

    return p, k, model


def resolve_config(step: str | None = None) -> tuple[str, str, str]:
    """Public resolver for agents that need provider-specific request shapes."""
    return _resolve(step)


class MissingKeyError(RuntimeError):
    """Raised when the configured provider requires an API key but none is set."""


def assert_llm_configured(step: str | None = None) -> None:
    """Raise MissingKeyError if the resolved provider needs a key and has none.

    Lets callers that must produce real LLM output (e.g. resume generation) fail
    loudly with an actionable message instead of silently emitting an empty
    structured result that downstream code mistakes for success. Keyless
    providers (ollama, claude_cli, codex_cli) always pass.
    """
    provider, key, _model = _resolve(step)
    if provider_needs_key(provider) and not key:
        raise MissingKeyError(
            f"No API key configured for provider {provider!r}. "
            "Add your key in Settings, or switch to a local provider."
        )


def _client_nvidia(k: str):
    return instructor.from_openai(
        _openai(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=k,
            timeout=_TIMEOUT,
            max_retries=0,
        ),
        mode=instructor.Mode.JSON,
    )


def _client_gemini(k: str):
    return _openai(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=k,
        timeout=_TIMEOUT,
        max_retries=0,
    )


# Sent on every OpenAI-compatible call (see _client_openai_compat).
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def _client_openai_compat(provider: str, key: str):
    return _openai(
        base_url=_provider_base_url(provider),
        api_key=key,
        timeout=_TIMEOUT,
        max_retries=0,
        # Cloudflare in front of some compat endpoints (opencode.ai/zen: "error code:
        # 1010") rejects SDK-default user agents regardless of key validity — verified
        # live 2026-09-04. A browser UA passes; harmless for every other endpoint.
        default_headers={"User-Agent": _BROWSER_UA},
    )


def call_llm(
    s: str,
    u: str,
    m: type[BaseModel],
    step: str | None = None,
    *,
    media: PdfMedia | None = None,
):
    """
    Call LLM with structured output.

    Pass `step` (e.g. "evaluator", "scout", "ingestor") to use that step's
    per-step provider/key/model settings. Omit for global defaults.

    Transient errors (rate limit, connection, 5xx) are retried with backoff.
    """
    def _attempt():
        try:
            if media is None:
                return _call_llm_once(s, u, m, step)
            return _call_llm_once(s, u, m, step, media=media)
        except Exception as exc:
            if not media or not media_rejection(exc):
                raise
            # A provider/model can reject an otherwise valid media shape. One
            # bounded text-only retry preserves reliable ingestion without
            # repeatedly resending a résumé or weakening auth/session headers.
            _log.warning("LLM media input rejected; retrying with extracted text only (step=%s)", step)
            return _call_llm_once(s, u, m, step, media=None)

    return _retry_llm_call(_attempt)


def _subscription_call(provider, attempt, fallback, *, step):
    """Run a subscription-CLI call, retrying once on a cold-start timeout, then
    falling back gracefully on any other CLI failure (not-installed, login,
    credit, malformed output). The first call after a sign-in/idle period often
    times out while the runtime warms up but succeeds on the retry."""
    from llm import subscription_cli as _sub
    try:
        try:
            return attempt()
        except _sub.CliTimeout:
            _log.warning("%s subscription CLI timed out (step=%s) — retrying once", provider, step)
            return attempt()
    except _sub.CliError as exc:
        _log.warning("%s subscription CLI failed (step=%s): %s", provider, step, exc)
        return fallback()


def _call_llm_once(
    s: str,
    u: str,
    m: type[BaseModel],
    step: str | None = None,
    *,
    media: PdfMedia | None = None,
):
    p, k, model = _resolve(step)

    if p == "anthropic":
        if not k:
            _log.warning("anthropic — no key (step=%s) — falling back", step)
            return _parse_fallback(u, m)
        anthropic_base = (get_setting("anthropic_base_url", "") or os.environ.get("ANTHROPIC_BASE_URL", "")).strip().rstrip("/")
        if anthropic_base:
            for suffix in ("/v1/messages", "/messages"):
                if anthropic_base.endswith(suffix):
                    anthropic_base = anthropic_base[:-len(suffix)].rstrip("/")
                    break
            anthropic_client = _anthropic(api_key=k, base_url=anthropic_base, timeout=120.0, max_retries=0)
        else:
            anthropic_client = _anthropic(api_key=k, timeout=120.0, max_retries=0)
        try:
            r = anthropic_client.messages.parse(
                model=model,
                max_tokens=4096,
                system=s,
                messages=[{"role": "user", "content": content_parts("messages", u, media)}],
                output_format=m,
            )
            return r.parsed_output
        except Exception as exc:
            if media and media_rejection(exc):
                raise
            schema = m.model_json_schema()
            system_prompt = (s + "\nReturn ONLY valid JSON matching this schema:\n" + json.dumps(schema)) if s else ("Return ONLY valid JSON matching this schema:\n" + json.dumps(schema))
            raw = _call_raw_once(system_prompt, u, step=step, media=media)
            return _extract_and_validate_json(raw, m)

    elif p == "groq":
        if not k:
            _log.warning("groq — no key (step=%s) — falling back", step)
            return _parse_fallback(u, m)
        groq_client = instructor.from_openai(
            _openai(base_url="https://api.groq.com/openai/v1", api_key=k,
                    timeout=_TIMEOUT, max_retries=0)
        )
        return groq_client.chat.completions.create(
            model=model,
            response_model=m,
            max_retries=1,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
        )

    elif p == "gemini":
        if not k:
            _log.warning("gemini: no key (step=%s); falling back", step)
            return _parse_fallback(u, m)
        gemini_client = instructor.from_openai(_client_gemini(k), mode=instructor.Mode.JSON)
        return gemini_client.chat.completions.create(
            model=model,
            response_model=m,
            max_retries=1,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
        )

    elif p == "nvidia":
        if not k:
            _log.warning("nvidia — no key (step=%s) — falling back", step)
            return _parse_fallback(u, m)
        nvidia_client = _client_nvidia(k)
        return nvidia_client.chat.completions.create(
            model=model,
            response_model=m,
            max_retries=1,
            max_tokens=16384,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )

    elif p == "openai":
        if not k:
            _log.warning("openai — no key (step=%s)", step)
            return _parse_fallback(u, m)
        openai_client = instructor.from_openai(_openai(api_key=k, timeout=_TIMEOUT, max_retries=0))
        return openai_client.chat.completions.create(
            model=model,
            response_model=m,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
        )

    elif p == "deepseek":
        if not k:
            _log.warning("deepseek — no key (step=%s)", step)
            return _parse_fallback(u, m)
        # deepseek-reasoner does not support tool_choice — use JSON mode instead
        mode = instructor.Mode.JSON if "reasoner" in model else instructor.Mode.TOOLS
        deepseek_client = instructor.from_openai(
            _openai(base_url="https://api.deepseek.com", api_key=k, timeout=_TIMEOUT, max_retries=0),
            mode=mode,
        )
        return deepseek_client.chat.completions.create(
            model=model,
            response_model=m,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
        )

    elif p in _OPENAI_COMPAT_PROVIDERS:
        if not k:
            _log.warning("%s — no key (step=%s)", p, step)
            return _parse_fallback(u, m)
        if p == "opencode":
            if media is None:
                return _call_opencode_llm(k, model, s, u, m)
            return _call_opencode_llm(k, model, s, u, m, media=media)
        if p == "custom":
            if media is None:
                return _call_custom_llm(k, model, s, u, m)
            return _call_custom_llm(k, model, s, u, m, media=media)
        if p == "perplexity":
            schema = m.model_json_schema()
            # _call_raw_once (not call_raw) — the outer _retry_llm_call already
            # wraps this call_llm invocation; nesting would multiply retries.
            raw = _call_raw_once(
                s + "\nReturn only valid JSON matching this schema:\n" + str(schema),
                u,
                step=step,
                media=media,
            )
            try:
                return m.model_validate_json(raw)
            except Exception:
                _log.warning("perplexity structured parse failed (step=%s)", step)
                return _parse_fallback(u, m)
        try:
            client = _client_openai_compat(p, k)
        except ValueError as exc:
            _log.warning("%s configuration invalid (step=%s): %s", p, step, exc)
            return _parse_fallback(u, m)
        compat_client = instructor.from_openai(
            client,
            mode=instructor.Mode.JSON,
        )
        return compat_client.chat.completions.create(
            model=model,
            response_model=m,
            max_retries=1,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
        )

    elif p in SUBSCRIPTION_CLI_PROVIDERS:
        # Subscription providers: shell out to the user's logged-in CLI (Claude /
        # Codex / Gemini / Copilot — no API key). The CLI returns text, so we ask
        # for schema-shaped JSON and parse.
        from llm import subscription_cli as _sub
        return _subscription_call(
            p, lambda: _sub.complete_structured(p, s, u, m, model=model),
            lambda: _parse_fallback(u, m), step=step,
        )

    else:  # ollama / default
        b = get_setting("ollama_url", "http://localhost:11434/v1")
        _log.info("ollama at %s model=%s (step=%s)", b, model, step)
        ollama_client = instructor.from_openai(
            _openai(base_url=b, api_key="ollama", timeout=_TIMEOUT, max_retries=0)
        )
        return ollama_client.chat.completions.create(
            model=model,
            response_model=m,
            max_retries=1,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
        )


def call_raw(
    s: str,
    u: str,
    step: str | None = None,
    *,
    media: PdfMedia | None = None,
) -> str:
    """
    Call LLM for free-form text output.

    Pass `step` (e.g. "generator") to use that step's per-step settings.

    Transient errors (rate limit, connection, 5xx) are retried with backoff.
    """
    def _attempt():
        try:
            if media is None:
                return _call_raw_once(s, u, step)
            return _call_raw_once(s, u, step, media=media)
        except Exception as exc:
            if not media or not media_rejection(exc):
                raise
            _log.warning("LLM media input rejected; retrying with extracted text only (step=%s)", step)
            return _call_raw_once(s, u, step, media=None)

    return _retry_llm_call(_attempt)


def _call_raw_once(
    s: str,
    u: str,
    step: str | None = None,
    *,
    media: PdfMedia | None = None,
) -> str:
    p, k, model = _resolve(step)

    if p == "anthropic":
        if not k:
            return ""
        anthropic_base = (get_setting("anthropic_base_url", "") or os.environ.get("ANTHROPIC_BASE_URL", "")).strip().rstrip("/")
        if anthropic_base:
            for suffix in ("/v1/messages", "/messages"):
                if anthropic_base.endswith(suffix):
                    anthropic_base = anthropic_base[:-len(suffix)].rstrip("/")
                    break
            anthropic_client = _anthropic(api_key=k, base_url=anthropic_base, timeout=120.0, max_retries=0)
        else:
            anthropic_client = _anthropic(api_key=k, timeout=120.0, max_retries=0)
        anthropic_response = anthropic_client.messages.create(
            model=model,
            max_tokens=4096,
            system=s,
            messages=[{"role": "user", "content": content_parts("messages", u, media)}],
        )
        text_parts = []
        for block in getattr(anthropic_response, "content", []):
            if getattr(block, "type", "") == "text" or hasattr(block, "text"):
                text_parts.append(getattr(block, "text", ""))
        return "".join(text_parts).strip()

    elif p == "groq":
        if not k:
            return ""
        groq_client = _openai(base_url="https://api.groq.com/openai/v1", api_key=k,
                   timeout=_TIMEOUT, max_retries=0)
        groq_response = groq_client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
        )
        return groq_response.choices[0].message.content or ""

    elif p == "gemini":
        if not k:
            return ""
        gemini_client = _client_gemini(k)
        gemini_response = gemini_client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
        )
        return gemini_response.choices[0].message.content or ""

    elif p == "nvidia":
        if not k:
            return ""
        nvidia_client = _openai(base_url="https://integrate.api.nvidia.com/v1", api_key=k,
                   timeout=_TIMEOUT, max_retries=0)
        nvidia_response = nvidia_client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
            max_tokens=16384,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        return nvidia_response.choices[0].message.content or ""

    elif p == "openai":
        if not k:
            return ""
        openai_client = _openai(api_key=k, timeout=_TIMEOUT, max_retries=0)
        openai_response = openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
        )
        return openai_response.choices[0].message.content or ""

    elif p == "deepseek":
        if not k:
            return ""
        deepseek_client = _openai(base_url="https://api.deepseek.com", api_key=k, timeout=_TIMEOUT, max_retries=0)
        deepseek_response = deepseek_client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
        )
        return deepseek_response.choices[0].message.content or ""

    elif p in _OPENAI_COMPAT_PROVIDERS:
        if not k:
            return ""
        if p == "opencode":
            if media is None:
                return _call_opencode_raw(k, model, s, u)
            return _call_opencode_raw(k, model, s, u, media=media)
        if p == "custom":
            if media is None:
                return _call_custom_raw(k, model, s, u)
            return _call_custom_raw(k, model, s, u, media=media)
        try:
            compat_raw_client = _client_openai_compat(p, k)
        except ValueError as exc:
            _log.warning("%s configuration invalid (step=%s): %s", p, step, exc)
            return ""
        # Perplexity included: it serves only the OpenAI-compatible
        # chat-completions API (no Responses API).
        compat_chat_response = compat_raw_client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
        )
        return compat_chat_response.choices[0].message.content or ""

    elif p in SUBSCRIPTION_CLI_PROVIDERS:
        from llm import subscription_cli as _sub
        return _subscription_call(
            p, lambda: _sub.complete_text(p, s, u, model=model), lambda: "", step=step,
        )

    else:  # ollama
        b = get_setting("ollama_url", "http://localhost:11434/v1")
        ollama_client = _openai(base_url=b, api_key="ollama", timeout=_TIMEOUT, max_retries=0)
        ollama_response = ollama_client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": content_parts("chat/completions", u, media)}],
        )
        return ollama_response.choices[0].message.content or ""


def _parse_fallback(u: str, m: type[BaseModel]):
    """Minimal local fallback — no LLM, returns an empty but VALID model.

    The instance MUST be valid: callers access attributes on it (e.g. the
    ingestor reads ``.n``), and a bare ``model_construct()`` omits required
    fields, which then raises ``AttributeError`` downstream. So when the model
    has required fields with no default, populate them with type-appropriate
    empties instead of leaving them unset.
    """
    try:
        return m()
    except Exception:
        pass
    empties: dict = {str: "", int: 0, float: 0.0, bool: False, list: [], dict: {}, tuple: ()}
    try:
        kwargs = {}
        for field_name, field_info in m.model_fields.items():
            if field_info.is_required():
                annotation = field_info.annotation
                origin = getattr(annotation, "__origin__", None) or annotation
                kwargs[field_name] = empties.get(origin, "")
        return m(**kwargs)
    except Exception as log_exc:
        logging.getLogger(__name__).warning('suppressed exception in _parse_fallback: %s', log_exc)
        return m.model_construct()
