"""Request dependencies — API-key auth (docs/07 §2)."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from galaxy.common.config import get_settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    """Validate the x-api-key header against the configured key set."""
    settings = get_settings()
    if not x_api_key or x_api_key not in settings.api_key_set:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing or invalid x-api-key"
        )
    return x_api_key
