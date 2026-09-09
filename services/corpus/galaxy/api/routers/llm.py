"""LLM proxy passthrough (docs/07 §4). API-key guarded.

Single OpenAI-compatible egress; injects stream:false. A per-key rate limit + max-tokens cap
guard against a buggy client draining a paid endpoint (docs/07 §6).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from galaxy.api.deps import require_api_key
from galaxy.api.throttle import throttle
from galaxy.llm.client import LLMClient

router = APIRouter(prefix="/llm", tags=["llm"], dependencies=[Depends(require_api_key), Depends(throttle)])

MAX_TOKENS_CAP = 4096


class ChatRequest(BaseModel):
    messages: list[dict]
    max_tokens: int = Field(default=1024, le=MAX_TOKENS_CAP)
    temperature: float = 0.2
    tools: list[dict] | None = None
    model: str | None = None


@router.post("/chat")
async def chat(req: ChatRequest) -> dict:
    try:
        return await LLMClient().chat(
            req.messages,
            max_tokens=min(req.max_tokens, MAX_TOKENS_CAP),
            temperature=req.temperature,
            tools=req.tools,
            model=req.model,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"llm upstream error: {exc}") from exc


@router.post("/probe")
async def probe() -> dict:
    variant = await LLMClient().probe()
    return {"variant": variant["name"] if variant else None, "tool_calling": variant is not None}
