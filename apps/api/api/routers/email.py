"""Email monitoring endpoints (§7).

Connect the address you apply from, test it, and poll it. Credentials are write-only over this API:
`status` reports whether a password is set but never returns it.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from api.dependencies import get_repository
from core.logging import get_logger
from email_monitor.service import (
    KEY_ENABLED,
    KEY_HOST,
    KEY_MAILBOX,
    KEY_PASSWORD,
    KEY_PORT,
    KEY_USERNAME,
    create_email_monitor_service,
)

_log = get_logger(__name__)


class ConnectRequest(BaseModel):
    host: str
    username: str
    # Optional so the user can change the mailbox or port without retyping the password.
    password: str | None = None
    port: int = 993
    mailbox: str = "INBOX"
    enabled: bool = True


def create_router(manager=None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/email", tags=["email"])

    @router.get("/status")
    async def status() -> dict:
        """Whether monitoring is configured and on. Never returns the password."""
        return await asyncio.to_thread(create_email_monitor_service().status)

    @router.post("/connect")
    async def connect(req: ConnectRequest) -> dict:
        """Save mailbox settings.

        An omitted password keeps the stored one, so editing the port or mailbox doesn't force the
        user to re-enter an app password they pasted from their provider and no longer have.
        """
        values = {
            KEY_HOST: req.host.strip(),
            KEY_USERNAME: req.username.strip(),
            KEY_PORT: str(req.port),
            KEY_MAILBOX: req.mailbox.strip() or "INBOX",
            KEY_ENABLED: "true" if req.enabled else "false",
        }
        if req.password:
            values[KEY_PASSWORD] = req.password
        repo = get_repository()
        await asyncio.to_thread(repo.settings.save_settings, values)
        return await asyncio.to_thread(create_email_monitor_service(repo).status)

    @router.post("/disconnect")
    async def disconnect() -> dict:
        """Turn monitoring off and clear the stored credential."""
        repo = get_repository()
        await asyncio.to_thread(
            repo.settings.save_settings, {KEY_ENABLED: "false", KEY_PASSWORD: ""}
        )
        return await asyncio.to_thread(create_email_monitor_service(repo).status)

    @router.post("/check")
    async def check() -> dict:
        """Test the connection without polling, for the Settings screen."""
        return await asyncio.to_thread(create_email_monitor_service().check)

    @router.post("/poll")
    async def poll(since_days: int = 14, force: bool = False) -> dict:
        """Run a monitoring pass now.

        `force` re-processes already-seen messages, which is how a classification fix gets applied to
        mail that arrived before it.
        """
        service = create_email_monitor_service()
        result = await asyncio.to_thread(service.poll, since_days=since_days, force=force)
        if manager is not None and result.updated:
            await manager.broadcast(
                {
                    "type": "agent",
                    "event": "email_outcomes",
                    "msg": f"Email monitoring updated {result.updated} application(s)",
                }
            )
        return {
            "scanned": result.scanned,
            "matched": result.matched,
            "updated": result.updated,
            "skipped_seen": result.skipped_seen,
            "outcomes": result.outcomes,
            "error": result.error,
            "enabled": result.enabled,
        }

    return router
