from __future__ import annotations

import asyncio
from importlib import import_module

from fastapi import APIRouter, HTTPException
from pydantic import Field

from api.rate_limit import RateLimiter, require_rate_limit
from core.types import StrictBody


class ConflictResolveBody(StrictBody):
    # Module level on purpose: a body model defined inside create_router()
    # under `from __future__ import annotations` demotes the parameter to a
    # query param (FastAPI ForwardRef/openapi trap).
    choice: str = Field(pattern="^(pick|keep_both|reopen)$")
    point_kind: str = Field(default="", max_length=16)
    point_id: str = Field(default="", max_length=128)


def _live_profile(repo):
    """Snapshot rows with point children guaranteed (legacy blobs derived on
    read). profile is a runtime import: the api->profile boundary forbids the
    static one (test_import_boundaries)."""
    try:
        return import_module("profile.ingest_parse").ensure_points(repo.profile.get_profile())
    except Exception:
        return repo.profile.get_profile()


def _unit_labels(profile: dict) -> dict[tuple[str, str], str]:
    """(kind, id) -> human label for selectable bullet children only.

    Entity titles/skill names are identity metadata, never conflict members.
    Keeping them out of this map also means the list endpoint prunes legacy
    entity-level memberships on every read.
    """
    labels: dict[tuple[str, str], str] = {}
    if not isinstance(profile, dict):
        return labels
    for key, kind in (("projects", "project"), ("exp", "experience")):
        for row in profile.get(key) or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            for point in row.get("points") or []:
                if isinstance(point, dict) and point.get("id"):
                    text = str(point.get("text") or "").strip()
                    # Conflict members are bullets; entity labels are rendered
                    # by the surrounding profile card, never mixed into the
                    # staged/conflict point text itself.
                    if text:
                        labels[(kind, str(point["id"]))] = text
    return labels


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["conflicts"])
    rescan_limiter = RateLimiter(2, 60)
    from data.repository import create_repository

    repo = create_repository()

    @router.get("/conflicts")
    async def list_conflicts(tag_id: str = "", status: str = "") -> dict:
        if status and status not in ("open", "picked", "keep_both", "dismissed"):
            raise HTTPException(400, "status must be open|picked|keep_both|dismissed")

        def _work() -> dict:
            profile = _live_profile(repo)
            labels = _unit_labels(profile)
            pruned = repo.conflicts.prune_stale(set(labels))
            excluded = repo.point_tags.scoped_point_ids(tag_id) if tag_id else {}

            def in_scope(kind: str, point_id: str) -> bool:
                out = excluded.get(kind)
                return not out or point_id not in out

            groups = []
            for group in repo.conflicts.list_groups():
                if status and group.get("status") != status:
                    continue
                members = []
                in_scope_count = 0
                for member in group.get("members") or []:
                    kind = member.get("point_kind", "")
                    point_id = member.get("point_id", "")
                    scoped_in = in_scope(kind, point_id)
                    in_scope_count += 1 if scoped_in else 0
                    members.append({
                        "point_kind": kind,
                        "point_id": point_id,
                        "label": labels.get((kind, point_id), point_id),
                        "in_scope": scoped_in,
                    })
                # With an active tag only groups that still constrain this
                # build matter; without a status filter hide the rest.
                if tag_id and not status and in_scope_count < 2:
                    continue
                members.sort(key=lambda m: m["point_id"])
                groups.append({
                    "id": group.get("id"),
                    "reason": group.get("reason"),
                    "score": group.get("score"),
                    "status": group.get("status"),
                    "picked_kind": group.get("picked_kind"),
                    "picked_id": group.get("picked_id"),
                    "in_scope_count": in_scope_count,
                    "members": members,
                })
            return {"groups": groups, "pruned": pruned}

        return await asyncio.to_thread(_work)

    @router.post("/conflicts/{group_id}/resolve")
    async def resolve_conflict(group_id: str, body: ConflictResolveBody) -> dict:
        def _work() -> dict:
            if body.choice == "pick":
                return repo.conflicts.resolve(group_id, body.point_kind, body.point_id)
            if body.choice == "keep_both":
                return repo.conflicts.keep_both(group_id)
            return repo.conflicts.reopen(group_id)

        try:
            group = await asyncio.to_thread(_work)
        except ValueError as exc:
            message = str(exc)
            if "not found" in message:
                raise HTTPException(404, message) from exc
            raise HTTPException(422, message) from exc
        return {"group": group}

    @router.post("/conflicts/{group_id}/dismiss")
    async def dismiss_conflict(group_id: str) -> dict:
        try:
            group = await asyncio.to_thread(repo.conflicts.dismiss, group_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"group": group}

    @router.post("/conflicts/rescan")
    async def rescan_conflicts() -> dict:
        require_rate_limit(rescan_limiter)
        from data.vector import embeddings

        def _work() -> dict:
            units = import_module("data.conflicts_detect").flatten_profile_units(_live_profile(repo))
            status = embeddings.embedding_status()
            degraded = bool(status.get("degraded")) or status.get("mode") == "hashing"
            summary = import_module("data.conflicts_detect").sync_detection(
                units,
                repo.conflicts,
                embed_fn=embeddings.embed_texts,
                semantic_advisory=degraded,
            )
            return {**summary, "units": len(units)}

        return await asyncio.to_thread(_work)

    return router
