from __future__ import annotations

import asyncio
import os
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_corpus_discovery_service, get_repository
from api.search_runtime import SEARCH_TASK, search_tasks
from api.scheduler import ensure_ghost_job
from core.types import PreferencesBody, ResetDataBody, SettingsBody, TemplateBody
from data.repository import Repository


MASK = "__JHM_SECRET_SET__"
LEGACY_BULLET_MASK = "\u2022" * 20
LEGACY_MOJIBAKE_BULLET_MASK = "\u00e2\u20ac\u00a2" * 20
LEGACY_DOUBLE_ENCODED_BULLET_MASK = "\u00c3\u00a2\u00e2\u201a\u00ac\u00c2\u00a2" * 20
LEGACY_MASKS = {
    MASK,
    LEGACY_BULLET_MASK,
    LEGACY_MOJIBAKE_BULLET_MASK,
    LEGACY_DOUBLE_ENCODED_BULLET_MASK,
}


def sensitive_keys(settings: dict) -> set:
    fixed = {"anthropic_key", "linkedin_cookie", "x_bearer_token"}
    dynamic = {key for key in settings if key.endswith("_api_key") or key.endswith("_key") or key.endswith("_token")}
    return fixed | dynamic


def _extract_reply_snippet(data: dict, endpoint_type: str = "chat/completions") -> str:
    reply = ""
    try:
        if endpoint_type == "responses":
            for item in data.get("output", []):
                if item.get("type") == "message":
                    for c in item.get("content", []):
                        if c.get("type") in ("output_text", "text") and c.get("text"):
                            reply += c.get("text")
        elif endpoint_type == "messages":
            for c in data.get("content", []):
                if c.get("type") == "text" and c.get("text"):
                    reply += c.get("text")
                elif c.get("type") == "thinking" and c.get("thinking"):
                    reply += c.get("thinking")
        else:
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                reply = msg.get("content") or msg.get("reasoning_content") or ""
    except Exception:
        pass
    return " ".join(reply.split())[:70]


def _extract_error_detail(response) -> tuple[str, str]:
    error_msg = ""
    error_type = ""
    try:
        data = response.json()
        if isinstance(data, dict):
            err_obj = data.get("error")
            if isinstance(err_obj, dict):
                error_msg = str(err_obj.get("message") or err_obj.get("detail") or "")
                error_type = str(err_obj.get("type") or "")
            elif isinstance(err_obj, str):
                error_msg = err_obj
            elif data.get("detail"):
                error_msg = str(data["detail"])
            elif data.get("message"):
                error_msg = str(data["message"])
            if not error_type and data.get("type"):
                error_type = str(data["type"])
    except Exception:
        text = response.text.strip()
        if "<html" in text.lower():
            error_msg = f"HTTP {response.status_code} (Endpoint returned non-API HTML page)"
        else:
            error_msg = text[:150]
    return error_msg.strip(), error_type.strip()


def _classify_error_status(status_code: int, error_msg: str, error_type: str = "") -> str:
    combined = f"{error_msg} {error_type}".lower()
    if "model" in combined and any(term in combined for term in ("not found", "not supported", "does not exist", "unknown model", "invalid model")):
        return "model_not_found"
    if status_code in {401, 403}:
        return "invalid_key"
    if status_code == 404:
        return "endpoint_not_found"
    if status_code == 429:
        return "rate_limited"
    if status_code == 400:
        return "invalid_format"
    if status_code >= 500:
        return "server_error"
    return "unreachable"


async def probe_provider_key(provider: str, key: str, settings: dict | None = None) -> dict:
    import httpx
    from llm import _OPENAI_COMPAT_BASE_URLS

    started = time.perf_counter()
    timeout = httpx.Timeout(20.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if provider == "anthropic":
                cfg = settings or {}
                base = str(cfg.get("anthropic_base_url") or "").strip().rstrip("/")
                for suffix in ("/v1/messages", "/messages"):
                    if base.endswith(suffix):
                        base = base[:-len(suffix)].rstrip("/")
                        break
                url = f"{base}/messages" if base else "https://api.anthropic.com/v1/messages"
                model = str(cfg.get("anthropic_model") or "claude-haiku-4-5-20251001").strip()
                try:
                    response = await client.post(
                        url,
                        headers={
                            "x-api-key": key,
                            "Authorization": f"Bearer {key}",
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                            "User-Agent": "Mozilla/5.0",
                        },
                        json={
                            "model": model,
                            "max_tokens": 15,
                            "messages": [{"role": "user", "content": "ping"}],
                        },
                    )
                except httpx.TimeoutException:
                    return {"status": "unreachable", "latency_ms": round((time.perf_counter() - started) * 1000), "model": model, "detail": "Connection timed out"}
                except Exception as exc:
                    return {"status": "unreachable", "latency_ms": round((time.perf_counter() - started) * 1000), "model": model, "detail": str(exc)[:120]}

                latency = round((time.perf_counter() - started) * 1000)
                if response.status_code == 200:
                    try:
                        reply = _extract_reply_snippet(response.json(), "messages")
                    except Exception:
                        reply = ""
                    return {"status": "ok", "latency_ms": latency, "model": model, "reply": reply}

                error_msg, error_type = _extract_error_detail(response)
                status = _classify_error_status(response.status_code, error_msg, error_type)
                return {"status": status, "latency_ms": latency, "model": model, "detail": error_msg or f"HTTP {response.status_code}"}

            elif provider == "openai":
                cfg = settings or {}
                model = str(cfg.get("openai_model") or "gpt-4o-mini").strip()
                try:
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"model": model, "max_tokens": 15, "messages": [{"role": "user", "content": "ping"}]},
                    )
                except httpx.TimeoutException:
                    return {"status": "unreachable", "latency_ms": round((time.perf_counter() - started) * 1000), "model": model, "detail": "Connection timed out"}
                except Exception as exc:
                    return {"status": "unreachable", "latency_ms": round((time.perf_counter() - started) * 1000), "model": model, "detail": str(exc)[:120]}

                latency = round((time.perf_counter() - started) * 1000)
                if response.status_code == 200:
                    try:
                        reply = _extract_reply_snippet(response.json(), "chat/completions")
                    except Exception:
                        reply = ""
                    return {"status": "ok", "latency_ms": latency, "model": model, "reply": reply}

                error_msg, error_type = _extract_error_detail(response)
                status = _classify_error_status(response.status_code, error_msg, error_type)
                return {"status": status, "latency_ms": latency, "model": model, "detail": error_msg or f"HTTP {response.status_code}"}

            elif provider == "groq":
                response = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                status = "ok" if response.status_code == 200 else "invalid_key" if response.status_code == 401 else "unreachable"
                error_msg, _ = _extract_error_detail(response) if response.status_code != 200 else ("", "")
                return {"status": status, "latency_ms": round((time.perf_counter() - started) * 1000), "detail": error_msg}

            elif provider == "gemini":
                response = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/openai/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                status = "ok" if response.status_code == 200 else "invalid_key" if response.status_code in {401, 403} else "unreachable"
                error_msg, _ = _extract_error_detail(response) if response.status_code != 200 else ("", "")
                return {"status": status, "latency_ms": round((time.perf_counter() - started) * 1000), "detail": error_msg}

            elif provider == "deepseek":
                response = await client.get(
                    "https://api.deepseek.com/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                status = "ok" if response.status_code == 200 else "invalid_key" if response.status_code in {401, 403} else "unreachable"
                error_msg, _ = _extract_error_detail(response) if response.status_code != 200 else ("", "")
                return {"status": status, "latency_ms": round((time.perf_counter() - started) * 1000), "detail": error_msg}

            elif provider == "openrouter":
                response = await client.get(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {key}"},
                )
                status = "ok" if response.status_code == 200 else "invalid_key" if response.status_code in {401, 403} else "unreachable"
                error_msg, _ = _extract_error_detail(response) if response.status_code != 200 else ("", "")
                return {"status": status, "latency_ms": round((time.perf_counter() - started) * 1000), "detail": error_msg}

            elif provider == "opencode":
                cfg = settings or {}
                raw_url = str(
                    cfg.get("opencode_base_url")
                    or os.environ.get("OPENCODE_BASE_URL", "")
                    or "https://opencode.ai/zen/go/v1"
                ).strip()
                endpoint_type = str(cfg.get("opencode_endpoint_type") or "").strip().lower()
                if not endpoint_type:
                    lowered = raw_url.lower().rstrip("/")
                    if lowered.endswith("/chat/completions"):
                        endpoint_type = "chat/completions"
                    elif (
                        lowered.endswith("/messages")
                        or str(cfg.get("opencode_protocol") or "").strip().lower() == "anthropic"
                    ):
                        endpoint_type = "messages"
                    else:
                        endpoint_type = "responses"

                from llm.client import opencode_headers, resolve_custom_endpoints

                target_url, _ = resolve_custom_endpoints(raw_url, endpoint_type)
                model = str(cfg.get("opencode_model") or "muse-spark-1.3-contributor").strip()
                reasoning = str(cfg.get("opencode_reasoning_effort") or "").strip().lower()
                headers = opencode_headers(key, anthropic_protocol=endpoint_type == "messages")

                if endpoint_type == "messages":
                    body = {
                        "model": model,
                        "max_tokens": 15,
                        "messages": [{"role": "user", "content": "ping"}],
                    }
                    if reasoning in ("low", "medium", "high"):
                        budget = {"low": 1024, "medium": 2048, "high": 4096}[reasoning]
                        body["thinking"] = {"type": "enabled", "budget_tokens": budget}
                        body["max_tokens"] = budget + 500
                elif endpoint_type == "chat/completions":
                    body = {
                        "model": model,
                        "max_tokens": 15,
                        "messages": [{"role": "user", "content": "ping"}],
                    }
                    if reasoning in ("low", "medium", "high"):
                        body["reasoning_effort"] = reasoning
                else:
                    body = {
                        "model": model,
                        "input": [{"role": "user", "content": "ping"}],
                    }
                    if reasoning in ("low", "medium", "high"):
                        body["reasoning"] = {"effort": reasoning}

                try:
                    response = await client.post(target_url, headers=headers, json=body)
                except httpx.TimeoutException:
                    return {
                        "status": "unreachable",
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                        "model": model,
                        "detail": "Connection timed out (server did not respond in 20s)",
                    }
                except httpx.ConnectError:
                    return {
                        "status": "unreachable",
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                        "model": model,
                        "detail": "Could not connect to host (connection refused or DNS failed)",
                    }
                except Exception as exc:
                    return {
                        "status": "unreachable",
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                        "model": model,
                        "detail": str(exc)[:120],
                    }

                latency = round((time.perf_counter() - started) * 1000)
                if response.status_code == 200:
                    reply = _extract_reply_snippet(response.json(), endpoint_type)
                    return {
                        "status": "ok",
                        "latency_ms": latency,
                        "model": model,
                        "reply": reply,
                        "detail": f"Model '{model}' responded successfully",
                    }
                error_msg, error_type = _extract_error_detail(response)
                return {
                    "status": _classify_error_status(response.status_code, error_msg, error_type),
                    "latency_ms": latency,
                    "model": model,
                    "detail": error_msg or f"HTTP {response.status_code}",
                }

            elif provider == "custom":
                cfg = settings or {}
                raw_url = str(
                    cfg.get("custom_base_url")
                    or os.environ.get("OPENAI_COMPAT_BASE_URL", "")
                ).strip().rstrip("/")
                if not raw_url:
                    return {"status": "endpoint_not_found", "latency_ms": 0, "detail": "Custom endpoint URL is missing"}

                endpoint_type = str(cfg.get("custom_endpoint_type", "")).strip().lower()
                if not endpoint_type:
                    if "/responses" in raw_url.lower():
                        endpoint_type = "responses"
                    elif "/messages" in raw_url.lower():
                        endpoint_type = "messages"
                    else:
                        endpoint_type = "chat/completions"

                from llm.client import resolve_custom_endpoints
                target_url, _ = resolve_custom_endpoints(raw_url, endpoint_type)

                model = str(cfg.get("custom_model") or "").strip()
                if not model:
                    return {
                        "status": "missing_model",
                        "latency_ms": 0,
                        "detail": "Please specify a model name to test",
                    }

                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0",
                }
                reasoning = str(cfg.get("custom_reasoning_effort") or "").strip().lower()

                if endpoint_type == "messages":
                    headers["x-api-key"] = key
                    headers["anthropic-version"] = "2023-06-01"
                    body = {
                        "model": model,
                        "max_tokens": 15,
                        "messages": [{"role": "user", "content": "ping"}],
                    }
                    if reasoning in ("low", "medium", "high"):
                        budget = 1024 if reasoning == "low" else 2048 if reasoning == "medium" else 4096
                        body["thinking"] = {"type": "enabled", "budget_tokens": budget}
                        body["max_tokens"] = budget + 500
                elif endpoint_type == "responses":
                    body = {
                        "model": model,
                        "input": [{"role": "user", "content": "ping"}],
                    }
                    if reasoning in ("low", "medium", "high"):
                        body["reasoning"] = {"effort": reasoning}
                else:  # chat/completions
                    body = {
                        "model": model,
                        "max_tokens": 15,
                        "messages": [{"role": "user", "content": "ping"}],
                    }
                    if reasoning in ("low", "medium", "high"):
                        body["reasoning_effort"] = reasoning

                try:
                    response = await client.post(target_url, headers=headers, json=body)
                except httpx.TimeoutException:
                    return {
                        "status": "unreachable",
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                        "model": model,
                        "detail": "Connection timed out (server did not respond in 20s)",
                    }
                except httpx.ConnectError:
                    return {
                        "status": "unreachable",
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                        "model": model,
                        "detail": "Could not connect to host (connection refused or DNS failed)",
                    }
                except Exception as exc:
                    return {
                        "status": "unreachable",
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                        "model": model,
                        "detail": str(exc)[:120],
                    }

                latency = round((time.perf_counter() - started) * 1000)
                if response.status_code == 200:
                    try:
                        reply = _extract_reply_snippet(response.json(), endpoint_type)
                    except Exception:
                        reply = ""
                    return {
                        "status": "ok",
                        "latency_ms": latency,
                        "model": model,
                        "reply": reply,
                        "detail": f"Model '{model}' responded successfully",
                    }

                error_msg, error_type = _extract_error_detail(response)
                status = _classify_error_status(response.status_code, error_msg, error_type)
                return {
                    "status": status,
                    "latency_ms": latency,
                    "model": model,
                    "detail": error_msg or f"HTTP {response.status_code}",
                }

            elif provider in _OPENAI_COMPAT_BASE_URLS:
                response = await client.get(
                    f"{_OPENAI_COMPAT_BASE_URLS[provider].rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                status = "ok" if response.status_code == 200 else "invalid_key" if response.status_code in {401, 403} else "unreachable"
                error_msg, _ = _extract_error_detail(response) if response.status_code != 200 else ("", "")
                return {"status": status, "latency_ms": round((time.perf_counter() - started) * 1000), "detail": error_msg}

            elif provider == "azure":
                cfg = settings or {}
                endpoint = str(
                    cfg.get("azure_openai_endpoint")
                    or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
                ).strip().rstrip("/")
                if not endpoint:
                    return {"status": "unchecked", "latency_ms": 0}
                if not endpoint.endswith("/openai/v1"):
                    endpoint = f"{endpoint}/openai/v1"
                response = await client.get(
                    f"{endpoint}/models",
                    headers={"api-key": key},
                )
                status = "ok" if response.status_code == 200 else "invalid_key" if response.status_code in {401, 403} else "unreachable"
                error_msg, _ = _extract_error_detail(response) if response.status_code != 200 else ("", "")
                return {"status": status, "latency_ms": round((time.perf_counter() - started) * 1000), "detail": error_msg}

            else:
                return {"status": "unchecked", "latency_ms": 0}
    except Exception as exc:
        return {"status": "unreachable", "latency_ms": round((time.perf_counter() - started) * 1000), "detail": str(exc)[:120]}


async def list_provider_models(provider: str, key: str, settings: dict | None = None) -> list[str]:
    import httpx
    from llm import _OPENAI_COMPAT_BASE_URLS

    cfg = settings or {}
    headers = {"Authorization": f"Bearer {key}", "User-Agent": "Mozilla/5.0"}
    url = ""
    if provider == "anthropic":
        base = str(cfg.get("anthropic_base_url") or "").strip().rstrip("/")
        for suffix in ("/v1/messages", "/messages"):
            if base.endswith(suffix):
                base = base[:-len(suffix)].rstrip("/")
                break
        url = f"{base}/models" if base else "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": key, "Authorization": f"Bearer {key}", "anthropic-version": "2023-06-01", "User-Agent": "Mozilla/5.0"}
    elif provider == "custom":
        raw = str(
            cfg.get("custom_base_url")
            or os.environ.get("OPENAI_COMPAT_BASE_URL", "")
            or "https://api.openai.com/v1"
        ).strip().rstrip("/")
        for suffix in ("/chat/completions", "/responses", "/messages"):
            if raw.endswith(suffix):
                raw = raw[:-len(suffix)].rstrip("/")
                break
        url = f"{raw}/models"
        headers = {"Authorization": f"Bearer {key}", "x-api-key": key, "User-Agent": "Mozilla/5.0"}
    elif provider == "opencode":
        from llm.client import opencode_headers

        raw = str(
            cfg.get("opencode_base_url")
            or os.environ.get("OPENCODE_BASE_URL", "")
            or "https://opencode.ai/zen/go/v1"
        ).strip().rstrip("/")
        for suffix in ("/chat/completions", "/responses", "/messages"):
            if raw.endswith(suffix):
                raw = raw[:-len(suffix)].rstrip("/")
                break
        url = f"{raw}/models"
        headers = opencode_headers(key)
    elif provider == "openai":
        url = "https://api.openai.com/v1/models"
    elif provider == "groq":
        url = "https://api.groq.com/openai/v1/models"
    elif provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/openai/models"
    elif provider == "nvidia":
        url = "https://integrate.api.nvidia.com/v1/models"
    elif provider == "deepseek":
        url = "https://api.deepseek.com/models"
    elif provider == "azure":
        endpoint = str(
            cfg.get("azure_openai_endpoint")
            or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        ).strip().rstrip("/")
        if not endpoint:
            return []
        if not endpoint.endswith("/openai/v1"):
            endpoint = f"{endpoint}/openai/v1"
        url = f"{endpoint}/models"
        headers = {"api-key": key}
    elif provider in _OPENAI_COMPAT_BASE_URLS:
        url = f"{_OPENAI_COMPAT_BASE_URLS[provider].rstrip('/')}/models"
    else:
        return []

    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
    rows = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
    ids: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, str):
                ids.append(row)
            elif isinstance(row, dict):
                model_id = row.get("id") or row.get("name") or row.get("model")
                if model_id:
                    ids.append(str(model_id))
    return sorted(dict.fromkeys(ids), key=str.lower)


def _settings_with_incoming(repo: Repository, incoming: dict | None) -> dict:
    cfg = repo.settings.get_settings()
    if not incoming:
        return cfg
    old = cfg
    cfg = {**cfg, **{key: "" if value is None else str(value) for key, value in incoming.items()}}
    for key in sensitive_keys(cfg):
        if cfg.get(key) in LEGACY_MASKS:
            cfg[key] = old.get(key, "")
    return cfg


def _provider_key(cfg: dict, provider: str) -> str:
    from llm import _ENV_NAMES, _KEY_NAMES

    key_name = _KEY_NAMES.get(provider, "")
    return str(
        cfg.get(key_name)
        or os.environ.get(_ENV_NAMES.get(provider, ""), "")
        or (os.environ.get("GOOGLE_API_KEY", "") if provider == "gemini" else "")
        or ""
    ).strip()


async def validate_provider_settings(repo: Repository, incoming: dict | None = None) -> dict:
    from llm import _KEY_NAMES, _OPENAI_COMPAT_BASE_URLS

    cfg = _settings_with_incoming(repo, incoming)
    probed = {"anthropic", "gemini", "openai", "groq", "deepseek", "azure", "openrouter", "opencode", "custom", *_OPENAI_COMPAT_BASE_URLS}
    all_providers = [
        "anthropic",
        "gemini",
        "openai",
        "groq",
        *[provider for provider in _KEY_NAMES if provider not in {"anthropic", "gemini", "openai", "groq"}],
    ]
    providers = [p for p in all_providers if _provider_key(cfg, p)]
    active = str(cfg.get("llm_provider") or "").strip().lower()
    if active and active in probed and active not in providers:
        providers.insert(0, active)

    async def one(provider: str):
        key = _provider_key(cfg, provider)
        if not key:
            return provider, {"status": "not_configured", "latency_ms": 0, "detail": "No API key configured"}
        if provider not in probed:
            return provider, {"status": "unchecked", "latency_ms": 0}
        return provider, await probe_provider_key(provider, key, cfg)

    pairs = await asyncio.gather(*(one(provider) for provider in providers))
    return {provider: result for provider, result in pairs}


def create_router(scheduler: AsyncIOScheduler, ghost_tick) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["settings"])

    @router.get("/template")
    async def get_template(repo: Repository = Depends(get_repository)):
        return {"template": await asyncio.to_thread(repo.settings.get_setting, "resume_template", "")}

    @router.post("/template")
    async def save_template(body: TemplateBody, repo: Repository = Depends(get_repository)):
        await asyncio.to_thread(repo.settings.save_settings, {"resume_template": body.template})
        return {"ok": True}

    @router.get("/preferences")
    async def get_preferences(repo: Repository = Depends(get_repository)):
        """The user's free-text 'what I'm looking for' — steers the scan and ranking."""
        return {"preferences": await asyncio.to_thread(repo.settings.get_setting, "job_preferences", "")}

    @router.post("/preferences")
    async def save_preferences(body: PreferencesBody, repo: Repository = Depends(get_repository)):
        await asyncio.to_thread(repo.settings.save_settings, {"job_preferences": body.preferences})
        return {"ok": True}

    @router.get("/settings")
    async def get_cfg(repo: Repository = Depends(get_repository)):
        settings = await asyncio.to_thread(repo.settings.get_settings)
        for key in sensitive_keys(settings):
            if settings.get(key):
                settings[key] = MASK
        return settings

    @router.get("/settings/validate")
    async def validate_settings(repo: Repository = Depends(get_repository)):
        return await validate_provider_settings(repo)

    @router.post("/settings/validate")
    async def validate_pending_settings(body: SettingsBody, repo: Repository = Depends(get_repository)):
        return await validate_provider_settings(repo, body.model_dump())

    async def _provider_models_response(provider: str, incoming: dict | None, repo: Repository):
        provider = provider.strip().lower()
        cfg = _settings_with_incoming(repo, incoming)
        key = _provider_key(cfg, provider)
        # ollama is local-only; its models come from the user's own server, not a
        # public catalog. Keep it free-form (the picker accepts any typed id).
        if provider == "ollama":
            return {"provider": provider, "models": [], "catalog": []}

        # The always-current models.dev catalog needs no API key, so the picker is
        # populated for browsing the moment a provider is chosen. When a key IS
        # set, the live /v1/models call (what the key can actually reach) is merged
        # IN FRONT, so the user's real models lead and the rest of the catalog
        # follows. Anything neither knows about can still be typed in free-form.
        from llm.model_catalog import catalog_for_provider

        catalog = await asyncio.to_thread(catalog_for_provider, provider)
        live: list[str] = []
        if key:
            try:
                live = await list_provider_models(provider, key, cfg)
            except Exception:
                live = []  # fall back to the catalog silently rather than erroring

        seen: set[str] = set()
        merged: list[str] = []
        for model_id in [*live, *[str(row["id"]) for row in catalog if row.get("id")]]:
            low = model_id.lower()
            if low and low not in seen:
                seen.add(low)
                merged.append(model_id)

        resp = {"provider": provider, "models": merged, "catalog": catalog}
        if not merged:
            resp["error"] = "not_configured" if not key else "unreachable"
        return resp

    @router.get("/settings/models/{provider}")
    async def get_provider_models(provider: str, repo: Repository = Depends(get_repository)):
        return await _provider_models_response(provider, None, repo)

    @router.post("/settings/models/{provider}")
    async def post_provider_models(provider: str, body: SettingsBody, repo: Repository = Depends(get_repository)):
        return await _provider_models_response(provider, body.model_dump(), repo)

    @router.get("/settings/subscription-status")
    async def subscription_status():
        """Install + login state for the subscription-CLI providers (no API key needed)."""
        from llm import SUBSCRIPTION_CLI_PROVIDERS, subscription_cli
        out = {}
        for p in sorted(SUBSCRIPTION_CLI_PROVIDERS):
            s = subscription_cli.status(p)
            if not s.get("installed"):
                s["install_hint"] = subscription_cli.install_hint(p)
            out[p] = s
        return out

    @router.post("/settings/subscription-login/{provider}")
    async def subscription_login(provider: str):
        """Launch the CLI's own browser sign-in; the UI then polls subscription-status."""
        from llm import SUBSCRIPTION_CLI_PROVIDERS, subscription_cli
        if provider not in SUBSCRIPTION_CLI_PROVIDERS:
            raise HTTPException(status_code=400, detail="unknown subscription provider")
        try:
            return subscription_cli.login(provider)
        except subscription_cli.CliNotInstalled as exc:
            return {"started": False, "error": "not_installed",
                    "hint": subscription_cli.install_hint(provider), "detail": str(exc)}

    @router.post("/settings")
    async def save_cfg(body: SettingsBody, repo: Repository = Depends(get_repository)):
        payload = {key: "" if value is None else str(value) for key, value in body.model_dump().items()}
        old = await asyncio.to_thread(repo.settings.get_settings)
        for key in sensitive_keys({**old, **payload}):
            if payload.get(key) in LEGACY_MASKS:
                payload[key] = old.get(key, "")
        try:
            await asyncio.to_thread(repo.settings.save_settings, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        from llm.client import reset_client_cache
        await asyncio.to_thread(reset_client_cache)
        if payload.get("ghost_mode") == "true":
            ensure_ghost_job(scheduler, ghost_tick)
        return {"ok": True}

    @router.delete("/settings/key/{key_name}")
    async def delete_key(key_name: str, repo: Repository = Depends(get_repository)):
        """Delete an API key or sensitive setting completely from storage."""
        deleted = await asyncio.to_thread(repo.settings.delete_setting, key_name)
        from llm.client import reset_client_cache
        await asyncio.to_thread(reset_client_cache)
        return {"ok": True, "key": key_name, "deleted": deleted}

    @router.post("/data/reset")
    async def reset_data(body: ResetDataBody):
        """Danger zone: wipe local data (leads, profile graph, vectors, generated
        documents) so the app can be reset for a clean test. Settings + provider
        config are kept unless ``clear_settings`` is set. Requires confirm=DELETE.

        A data-only reset keeps the reusable corpus and clears only user-authored
        scrape phrases. The full factory reset (``clear_settings``) also purges the
        corpus — jobs, observations, applications, and history — so "Delete
        everything" cannot leave hundreds of scraped jobs behind.
        """
        from data.maintenance import reset_all_data

        # Prevent an in-flight search from repopulating leads immediately after they are wiped.
        search_stopped = await search_tasks.stop(SEARCH_TASK)
        if search_stopped:
            await search_tasks.join(SEARCH_TASK)
        summary = await asyncio.to_thread(reset_all_data, clear_settings=body.clear_settings)
        corpus = get_corpus_discovery_service()
        if body.clear_settings:
            corpus_reset = await corpus.purge_corpus()
            summary["corpus_purged"] = corpus_reset
        else:
            corpus_reset = await corpus.scrape_clear_history()
            summary["scrape_history"] = corpus_reset
        summary["search_stopped"] = search_stopped
        if not corpus_reset.get("available", True):
            label = "corpus purge" if body.clear_settings else "corpus scrape history"
            summary["errors"].append(
                f"{label}: {corpus_reset.get('error') or 'corpus unavailable'}"
            )
        if body.clear_settings:
            # Drop cached LLM clients so a wiped provider config isn't reused. (Done
            # here in the api layer — the data layer must not import llm.)
            from llm.client import reset_client_cache

            await asyncio.to_thread(reset_client_cache)
        return {"ok": True, "summary": summary}

    return router
