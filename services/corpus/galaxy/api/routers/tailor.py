"""Tailoring + generated-asset endpoints (docs/05, docs/07 §3). API-key guarded."""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from galaxy.api.deps import require_api_key
from galaxy.api.routers.profile import _user
from galaxy.api.throttle import throttle
from galaxy.generation import service
from galaxy.generation.assets import load_asset

router = APIRouter(tags=["tailor"], dependencies=[Depends(require_api_key), Depends(throttle)])


class TailorRequest(BaseModel):
    job_id: str
    check_live: bool = True
    reframe: bool = False  # opt-in LLM bullet rewording, guard-vetted (docs/05 §3)


@router.post("/tailor")
async def tailor_route(req: TailorRequest, user_id: UUID = Depends(_user)) -> dict:
    try:
        return await service.tailor(
            user_id, req.job_id, check_live=req.check_live, reframe=req.reframe
        )
    except service.JobExpired as exc:
        raise HTTPException(status_code=409, detail="job posting is no longer open") from exc
    except service.JobNotFound as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except service.ProfileMissing as exc:
        raise HTTPException(status_code=400, detail="no profile set for this user") from exc


class OverrideRequest(BaseModel):
    job_id: str
    pin: list[str] = []
    drop: list[str] = []


@router.post("/tailor/override")
async def override_route(req: OverrideRequest, user_id: UUID = Depends(_user)) -> dict:
    try:
        return await service.override(user_id, req.job_id, req.pin, req.drop)
    except service.JobNotFound as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except service.ProfileMissing as exc:
        raise HTTPException(status_code=400, detail="no profile set for this user") from exc


class RenderRequest(BaseModel):
    job_id: str
    format: str = "pdf"  # pdf | docx | html | md
    kind: str = "resume"  # resume | cover


@router.post("/tailor/render")
async def render_route(req: RenderRequest, user_id: UUID = Depends(_user)) -> dict:
    try:
        return await service.render_asset(user_id, req.job_id, req.format, req.kind)
    except service.JobNotFound as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except service.ProfileMissing as exc:
        raise HTTPException(status_code=400, detail="no profile set for this user") from exc


class CompileRequest(BaseModel):
    """User-edited LaTeX from the in-UI editor."""

    source: str


@router.post("/tailor/compile")
async def compile_route(req: CompileRequest) -> Response:
    """Compile edited LaTeX and return the PDF, or the errors that stopped it.

    §5's in-UI editor exists so the user can fix mistakes, which requires showing them what broke.
    A failure returns 422 with the extracted log rather than a 500: a syntax error in someone's
    résumé is an expected outcome of editing, not a server fault.
    """
    from galaxy.generation.compile_tex import compile_latex

    result = await asyncio.to_thread(compile_latex, req.source)
    if result.ok and result.pdf:
        return Response(
            content=result.pdf,
            media_type="application/pdf",
            headers={"content-disposition": 'inline; filename="resume.pdf"'},
        )
    return JSONResponse(
        status_code=422,
        content={"error": result.error, "log": result.log, "engine": result.engine},
    )


@router.get("/tailor/compile/available")
async def compile_available_route() -> dict:
    """Whether a local LaTeX engine exists, so the UI can hide preview instead of failing at it."""
    from galaxy.generation.compile_tex import available_engine

    engine = available_engine()
    return {"available": engine is not None, "engine": engine}


@router.get("/assets/{ref}")
async def asset_route(ref: str) -> FileResponse:
    found = load_asset(ref)
    if not found:
        raise HTTPException(status_code=404, detail="asset not found or expired")
    path, content_type = found
    return FileResponse(path, media_type=content_type, filename=path.name)
