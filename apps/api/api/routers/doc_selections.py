from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import Field

from core.types import StrictBody


class SelectionPayload(StrictBody):
    # Module level on purpose: a body model defined inside create_router()
    # under `from __future__ import annotations` demotes the parameter to a
    # query param (FastAPI ForwardRef/openapi trap).
    version: int = 1
    sections: list[str] = Field(default_factory=list, max_length=16)
    experience_order: list[str] = Field(default_factory=list, max_length=64)
    experience_on: list[str] = Field(default_factory=list, max_length=64)
    projects_order: list[str] = Field(default_factory=list, max_length=64)
    projects_on: list[str] = Field(default_factory=list, max_length=64)
    points_on: list[str] = Field(default_factory=list, max_length=512)
    skills_on: list[str] = Field(default_factory=list, max_length=256)
    entity_titles: dict[str, str] = Field(default_factory=dict, max_length=128)


class DocSelectionBody(StrictBody):
    job_id: str = Field(default="", max_length=160)
    tag_id: str = Field(default="", max_length=32)
    name: str = Field(default="", max_length=80)
    selection: SelectionPayload


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["doc-selections"])
    from data.repository import create_repository

    repo = create_repository()

    @router.get("/doc-selections")
    async def get_selection(job_id: str = "", tag_id: str = "") -> dict:
        selection = await asyncio.to_thread(
            repo.doc_selections.get, job_id or "", tag_id or ""
        )
        return {"selection": selection}

    @router.put("/doc-selections")
    async def put_selection(body: DocSelectionBody) -> dict:
        result = await asyncio.to_thread(
            repo.doc_selections.upsert,
            body.job_id,
            body.tag_id,
            body.selection.model_dump(),
            body.name,
        )
        return result

    @router.delete("/doc-selections/{job_id}")
    async def delete_selection(job_id: str, tag_id: str = "") -> dict:
        deleted = await asyncio.to_thread(
            repo.doc_selections.delete, job_id or "", tag_id or ""
        )
        if not deleted:
            raise HTTPException(404, "no saved selection for this scope")
        return {"ok": True}

    @router.get("/doc-presets")
    async def list_presets() -> dict:
        presets = await asyncio.to_thread(repo.doc_selections.list_presets)
        return {"presets": presets}

    @router.post("/doc-presets")
    async def save_preset(body: DocSelectionBody) -> dict:
        if not body.name.strip():
            raise HTTPException(422, "preset name is required")
        try:
            return await asyncio.to_thread(
                repo.doc_selections.save_preset,
                body.name,
                body.tag_id,
                body.selection.model_dump(),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete("/doc-presets/{preset_id}")
    async def delete_preset(preset_id: str) -> dict:
        deleted = await asyncio.to_thread(repo.doc_selections.delete_preset, preset_id)
        if not deleted:
            raise HTTPException(404, "preset not found")
        return {"ok": True}

    return router
