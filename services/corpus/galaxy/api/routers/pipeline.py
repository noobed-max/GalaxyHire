"""Phase 6 pipeline API: applications, saved searches, insights, stats (docs/10). API-key guarded."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from galaxy.api.deps import require_api_key
from galaxy.api.routers.profile import _user
from galaxy.api.throttle import throttle
from galaxy.search import pipeline_store as ps
from galaxy.search import service
from galaxy.search.ranker import to_dict

router = APIRouter(tags=["pipeline"], dependencies=[Depends(require_api_key), Depends(throttle)])


# --- applications -----------------------------------------------------------


@router.get("/applications")
async def list_applications(user_id: UUID = Depends(_user)) -> dict:
    apps = await ps.list_applications(user_id)
    return {"count": len(apps), "applications": apps}


class StatusPatch(BaseModel):
    status: str
    note: str | None = None


@router.patch("/applications/{application_id}")
async def patch_application(
    application_id: UUID, body: StatusPatch, user_id: UUID = Depends(_user)
) -> dict:
    ok = await ps.set_status(user_id, application_id, body.status, body.note)
    if not ok:
        raise HTTPException(status_code=400, detail="unknown status or application not found")
    return {"ok": True}


class PrepareRequest(BaseModel):
    job_id: str
    check_live: bool = True
    reframe: bool = False
    # §6: the user chooses where résumés land. Absolute paths only — see resolve_output_dir.
    output_dir: str | None = None


@router.post("/applications/prepare")
async def prepare_application(req: PrepareRequest, user_id: UUID = Depends(_user)) -> dict:
    """Apply-prepare (docs/11 §W1.4): tailor + render the resume to disk + persist the per-job fill
    context. The web UI opens `apply_url` from the response; the extension reads the context later."""
    from galaxy.generation import service as gen

    try:
        return await gen.prepare(
            user_id,
            req.job_id,
            check_live=req.check_live,
            reframe=req.reframe,
            output_dir=req.output_dir,
        )
    except gen.JobExpired as exc:
        raise HTTPException(status_code=409, detail="job posting is no longer open") from exc
    except gen.JobNotFound as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except gen.ProfileMissing as exc:
        raise HTTPException(status_code=400, detail="no profile set for this user") from exc


@router.get("/applications/fill-context")
async def fill_context(url: str | None = None, user_id: UUID = Depends(_user)) -> dict:
    """The fill-only extension reads this for the active tab (docs/11 §W1.5)."""
    ctx = await ps.get_fill_context(user_id, url)
    if ctx is None:
        raise HTTPException(status_code=404, detail="no prepared application")
    return ctx


@router.get("/stats")
async def stats(user_id: UUID = Depends(_user)) -> dict:
    return await ps.pipeline_stats(user_id)


# --- saved searches ---------------------------------------------------------


class SaveSearchRequest(BaseModel):
    name: str
    params: dict


@router.post("/searches")
async def save_search(req: SaveSearchRequest, user_id: UUID = Depends(_user)) -> dict:
    return await ps.save_search(user_id, req.name, req.params)


@router.get("/searches")
async def list_searches(user_id: UUID = Depends(_user)) -> dict:
    return {"searches": await ps.list_searches(user_id)}


@router.post("/searches/{search_id}/run")
async def run_search(
    search_id: UUID, only_new: bool = True, user_id: UUID = Depends(_user)
) -> dict:
    saved = await ps.get_search(user_id, search_id)
    if not saved:
        raise HTTPException(status_code=404, detail="saved search not found")
    params = dict(saved["params"])
    # "only new since last run" → freshness window from the previous run timestamp
    if only_new and saved.get("last_run_at"):
        params["seen_after"] = saved["last_run_at"].isoformat()
    ranked = await service.search(user_id=user_id, **_search_kwargs(params))
    await ps.mark_search_run(user_id, search_id)
    return {"count": len(ranked), "results": [to_dict(j) for j in ranked]}


def _search_kwargs(params: dict) -> dict:
    """Whitelist saved params to service.search kwargs."""
    allowed = {
        "search_term", "positive_phrases", "positive_skills", "negative_titles",
        "negative_phrases", "max_years", "max_seniority", "remote", "location",
        "blacklist", "limit", "sort", "seen_after",
    }
    return {k: v for k, v in params.items() if k in allowed}


# --- insights ---------------------------------------------------------------


@router.get("/insights/reposts")
async def reposts(days: int = 90, min_count: int = 2) -> dict:
    return {"reposts": await ps.reposts(days=days, min_count=min_count)}


@router.get("/insights/skill-gap")
async def skill_gap(user_id: UUID = Depends(_user)) -> dict:
    return {"skill_gap": await ps.skill_gap(user_id)}
