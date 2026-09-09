from __future__ import annotations
import logging

import asyncio

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from pydantic import Field

from api.dependencies import get_generation_service, get_job_runner, get_repository
from api.rate_limit import RateLimiter, require_rate_limit
from core.generation_readiness import lead_generation_blocker
from core.types import StrictBody
from data.repository import Repository

_background_tasks: set[asyncio.Task] = set()

# M4: how long to ask the client to wait before retrying a transient failure.
_GENERATION_RETRY_AFTER_SECONDS = 30


class SelectionPayload(StrictBody):
    # Module level: a body model defined inside create_router() under
    # `from __future__ import annotations` demotes the parameter to a query
    # param (FastAPI ForwardRef/openapi trap).
    version: int = 1
    sections: list[str] = Field(default_factory=list, max_length=16)
    experience_order: list[str] = Field(default_factory=list, max_length=64)
    experience_on: list[str] = Field(default_factory=list, max_length=64)
    projects_order: list[str] = Field(default_factory=list, max_length=64)
    projects_on: list[str] = Field(default_factory=list, max_length=64)
    points_on: list[str] = Field(default_factory=list, max_length=512)
    skills_on: list[str] = Field(default_factory=list, max_length=256)


class GenerateBody(StrictBody):
    # Optional body: existing callers that POST query params only keep working.
    selection: SelectionPayload | None = None


def _selection_from_body(body: GenerateBody | None) -> dict | None:
    if body is None or body.selection is None:
        return None
    return body.selection.model_dump()


def _track_background_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _is_transient_generation_error(exc: Exception) -> bool:
    """Classify a generation failure as transient (worth retrying) vs permanent.

    Transient: network/timeout issues and retryable LLM errors (rate limit,
    connection, 5xx) that survived the client's own retries. Permanent: bad
    template, invalid lead, parsing errors — retrying won't help.
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    try:
        from llm.client import is_transient_llm_error
    except ImportError:
        return False
    return is_transient_llm_error(exc)


async def _hydrate_thin_description(job_id: str, lead: dict, repo) -> dict:
    """List-page results carry a 4000-char excerpt (or nothing at all — zero-token
    scrapers). When a lead is corpus-backed and its description is too thin to
    tailor against, fetch the FULL posting from the corpus detail endpoint and
    persist it. Fail-open: generation proceeds with whatever the lead has."""
    import re as _re

    meta = lead.get("source_meta") if isinstance(lead.get("source_meta"), dict) else {}
    corpus_job_id = str(meta.get("corpus_job_id") or "")
    description = str(lead.get("description") or "")
    non_url = _re.sub(r"https?://\S+|www\.\S+", "", description, flags=_re.I).strip()
    if not corpus_job_id or meta.get("description_hydrated") or len(non_url) >= 200:
        return lead
    try:
        from corpus.client import CorpusClient

        detail = await CorpusClient().get_job(corpus_job_id)
        if not isinstance(detail, dict):
            return lead
        full = str(detail.get("description_md") or detail.get("description") or "").strip()
        if len(_re.sub(r"https?://\S+|www\.\S+", "", full, flags=_re.I).strip()) <= len(non_url):
            return lead
        updated = await asyncio.to_thread(repo.leads.update_lead_description, job_id, full)
        if updated:
            logging.getLogger(__name__).info(
                "hydrated thin description for %s from corpus detail (%d -> %d chars)",
                job_id[:12], len(description), len(full),
            )
            return updated
    except Exception as exc:
        logging.getLogger(__name__).warning("description hydration skipped for %s: %s", job_id[:12], exc)
    return lead


async def generate_one(
    job_id: str,
    manager,
    *,
    repo: Repository | None = None,
    service=None,
    job_store=None,
    template_id: str = "",
    tag_id: str = "",
    selection: dict | None = None,
    contact_kinds: list[str] | None = None,
    show_location: bool | None = None,
) -> dict:
    repo = repo or get_repository()
    service = service or get_generation_service()
    job_store = job_store or get_job_runner()
    # Every repo/job_store call below is synchronous SQLite; run each off the
    # event loop so a locked/busy DB can't stall all other coroutines (and WS
    # broadcasts) for the SQLite busy-timeout.
    job = await asyncio.to_thread(job_store.create, "generate_package", {"job_id": job_id})
    lead = await asyncio.to_thread(repo.leads.get_lead_by_id, job_id)
    if not lead:
        await manager.broadcast({"type": "agent", "event": "gen_error", "msg": f"Lead {job_id} not found"})
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = await _hydrate_thin_description(job_id, lead, repo)
    blocked_reason = lead_generation_blocker(lead)
    if blocked_reason:
        try:
            await asyncio.to_thread(job_store.update, job.job_id, status="failed", error=blocked_reason)
        except Exception as log_exc:
            logging.getLogger(__name__).warning('suppressed exception in generate_one: %s', log_exc)
            pass
        await manager.broadcast({"type": "agent", "event": "gen_error", "msg": blocked_reason})
        raise HTTPException(status_code=422, detail=blocked_reason)

    # Resolve the resume template: explicit selection -> default template ->
    # legacy `resume_template` setting -> built-in layout (empty string).
    try:
        template = await asyncio.to_thread(repo.resume_templates.resolve_template_content, template_id)
    except Exception as log_exc:
        logging.getLogger(__name__).warning('suppressed exception in generate_one: %s', log_exc)
        if template_id:
            # The user explicitly picked this template; silently generating
            # with a different layout would misrepresent the output.
            raise HTTPException(status_code=422, detail=f"Resume template {template_id!r} could not be loaded") from None
        template = await asyncio.to_thread(repo.settings.get_setting, "resume_template", "")

    # Tag scoping: when a profile/tag is chosen, its most recent cover letter
    # becomes the rewrite base (the candidate's own letter, retargeted — never
    # reinvented).
    cover_letter_base = ""
    if tag_id:
        cl_doc = await asyncio.to_thread(repo.documents.latest_for_tag, "cover_letter", tag_id)
        if cl_doc:
            cover_letter_base = cl_doc.get("excerpt") or ""
            if not cover_letter_base and cl_doc.get("file_path", "").endswith((".txt", ".md")):
                try:
                    from pathlib import Path as _P

                    cover_letter_base = _P(cl_doc["file_path"]).read_text(encoding="utf-8", errors="replace")[:8000]
                except Exception as log_exc:
                    logging.getLogger(__name__).warning('suppressed exception in generate_one: %s', log_exc)

    await manager.broadcast({
        "type": "agent",
        "event": "gen_start",
        "msg": f"Generating for {lead.get('title','?')} @ {lead.get('company','?')}",
    })

    try:
        await asyncio.to_thread(job_store.update, job.job_id, status="running", progress=10)
        try:
            await asyncio.to_thread(repo.leads.update_lead_status, job_id, "tailoring")
            tailoring_lead = {**lead, "status": "tailoring"}
            await manager.broadcast({"type": "LEAD_UPDATED", "data": tailoring_lead})
        except Exception as log_exc:
            logging.getLogger(__name__).warning('suppressed exception in generate_one: %s', log_exc)
            pass
        generation = await service.generate_with_contacts(lead, template=template, cover_letter_base=cover_letter_base, tag_id=tag_id, selection=selection, contact_kinds=contact_kinds, show_location=show_location)
        package = generation.package
        persistence_errors: list[str] = []
        try:
            await asyncio.to_thread(
                repo.leads.save_asset_package,
                job_id,
                package["resume"],
                package["cover_letter"],
                package.get("selected_projects", []),
                package.get("keyword_coverage", {}),
            )
        except Exception as exc:
            # save_asset_package is the only write that flips the lead to
            # "approved" and records asset_path. If it fails, the UI would show
            # success while the DB still says "tailoring" with no resume —
            # treat the whole generation as failed instead.
            raise RuntimeError(f"generated assets could not be saved: {exc}") from exc

        outreach_fields = {}
        if package.get("founder_message"):
            outreach_fields["outreach_reply"] = package["founder_message"]
        if package.get("linkedin_note"):
            outreach_fields["outreach_dm"] = package["linkedin_note"]
        if package.get("cold_email"):
            outreach_fields["outreach_email"] = package["cold_email"]
        if outreach_fields:
            try:
                await asyncio.to_thread(repo.leads.update_outreach_fields, job_id, outreach_fields)
            except Exception as exc:
                logging.getLogger(__name__).warning('suppressed exception in generate_one: %s', exc)
                persistence_errors.append(f"outreach fields: {exc}")

        enriched_lead = {
            **lead,
            "asset": package["resume"],
            "resume_asset": package["resume"],
            "cover_letter_asset": package["cover_letter"],
            "selected_projects": package.get("selected_projects", []),
            "keyword_coverage": package.get("keyword_coverage", {}),
            "outreach_reply": package.get("founder_message", lead.get("outreach_reply", "")),
            "outreach_dm": package.get("linkedin_note", lead.get("outreach_dm", "")),
            "outreach_email": package.get("cold_email", lead.get("outreach_email", "")),
            "status": "approved",
        }
        contact_lookup = generation.contact_lookup or {}
        try:
            await asyncio.to_thread(repo.leads.save_contact_lookup, job_id, contact_lookup)
        except Exception as exc:
            logging.getLogger(__name__).warning('suppressed exception in generate_one: %s', exc)
            persistence_errors.append(f"contact lookup: {exc}")
        enriched_lead["contact_lookup"] = contact_lookup
        enriched_meta = dict(enriched_lead.get("source_meta") or {})
        enriched_meta["contact_lookup"] = contact_lookup
        if persistence_errors:
            enriched_meta["generation_persistence_errors"] = persistence_errors
        enriched_lead["source_meta"] = enriched_meta
        await manager.broadcast({"type": "LEAD_UPDATED", "data": enriched_lead})
        await manager.broadcast({
            "type": "agent",
            "event": "gen_done",
            "msg": f"Resume and cover letter ready: {lead.get('title','?')}",
        })
        try:
            await asyncio.to_thread(job_store.update, job.job_id, status="succeeded", progress=100, result={"lead": enriched_lead})
        except Exception as log_exc:
            logging.getLogger(__name__).warning('suppressed exception in generate_one: %s', log_exc)
            pass
        enriched_lead["generation_job_id"] = job.job_id
        return enriched_lead
    except Exception as exc:
        try:
            await asyncio.to_thread(job_store.update, job.job_id, status="failed", error=str(exc))
        except Exception as log_exc:
            logging.getLogger(__name__).warning('suppressed exception in generate_one: %s', log_exc)
        # M4: keep transient failures (network/rate-limit) in "tailoring" so the
        # user can simply retry, and surface a retry_after hint. Only permanent
        # failures (bad template/invalid lead) fall back to "discovered".
        transient = _is_transient_generation_error(exc)
        revert_status = "tailoring" if transient else "discovered"
        try:
            await asyncio.to_thread(repo.leads.update_lead_status, job_id, revert_status)
            failed_lead = {**lead, "status": revert_status}
            failed_meta = dict(failed_lead.get("source_meta") or {})
            failed_meta["generation_error"] = str(exc)
            if transient:
                failed_meta["retry_after"] = _GENERATION_RETRY_AFTER_SECONDS
            failed_lead["source_meta"] = failed_meta
            await manager.broadcast({"type": "LEAD_UPDATED", "data": failed_lead})
        except Exception as log_exc:
            logging.getLogger(__name__).warning('suppressed exception in generate_one: %s', log_exc)
            pass
        await manager.broadcast({
            "type": "agent",
            "event": "gen_error",
            "msg": f"Generation failed for {lead.get('title','?')}: {exc}",
        })
        status_code = 503 if transient else 500
        headers = {"Retry-After": str(_GENERATION_RETRY_AFTER_SECONDS)} if transient else None
        raise HTTPException(status_code=status_code, detail="Generation failed. See the activity log for details.", headers=headers) from exc


def create_router(*, manager) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["generation"])
    generate_limiter = RateLimiter(5, 60)

    @router.post("/leads/{job_id}/generate")
    async def generate_for_lead(
        job_id: str,
        template_id: str = "",
        tag_id: str = "",
        contact: list[str] | None = Query(None),
        show_location: bool | None = Query(None),
        body: GenerateBody | None = Body(None),
        repo: Repository = Depends(get_repository),
        service=Depends(get_generation_service),
    ):
        require_rate_limit(generate_limiter)
        lead = await generate_one(
            job_id, manager, repo=repo, service=service,
            template_id=template_id, tag_id=tag_id,
            selection=_selection_from_body(body),
            contact_kinds=contact, show_location=show_location,
        )
        return {"status": "ready", "job_id": job_id, "lead": lead, "generation_job_id": lead.get("generation_job_id", "")}

    @router.post("/leads/{job_id}/generate/start")
    async def start_generate_for_lead(
        job_id: str,
        template_id: str = "",
        tag_id: str = "",
        contact: list[str] | None = Query(None),
        show_location: bool | None = Query(None),
        body: GenerateBody | None = Body(None),
        repo: Repository = Depends(get_repository),
        service=Depends(get_generation_service),
        job_store=Depends(get_job_runner),
    ):
        require_rate_limit(generate_limiter)
        lead = await asyncio.to_thread(repo.leads.get_lead_by_id, job_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        tailoring_lead = {**lead, "status": "tailoring"}
        try:
            await asyncio.to_thread(repo.leads.update_lead_status, job_id, "tailoring")
            await manager.broadcast({"type": "LEAD_UPDATED", "data": tailoring_lead})
        except Exception as log_exc:
            logging.getLogger(__name__).warning('suppressed exception in start_generate_for_lead: %s', log_exc)
            pass
        selection = _selection_from_body(body)

        async def _run():
            try:
                await generate_one(
                    job_id, manager, repo=repo, service=service, job_store=job_store,
                    template_id=template_id, tag_id=tag_id, selection=selection,
                    contact_kinds=contact, show_location=show_location,
                )
            except Exception as log_exc:
                logging.getLogger(__name__).warning('suppressed exception in _run: %s', log_exc)
                pass

        _track_background_task(asyncio.create_task(_run()))
        return {"status": "started", "job_id": job_id, "lead": tailoring_lead}

    @router.post("/leads/{job_id}/suggest-selection")
    async def suggest_selection_for_lead(
        job_id: str,
        tag_id: str = "",
        repo: Repository = Depends(get_repository),
        service=Depends(get_generation_service),
    ):
        """AI pick + ATS rewrite suggestions for the resume maker (no drafting).

        Returns selection ids the pane applies to its own state plus point-level
        rewrite proposals rendered beside their target point. Picks reference only
        the tag-scoped profile the pane already shows; unknown ids are dropped.
        """
        require_rate_limit(generate_limiter)
        lead = await asyncio.to_thread(repo.leads.get_lead_by_id, job_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        lead = await _hydrate_thin_description(job_id, lead, repo)
        blocked_reason = lead_generation_blocker(lead)
        if blocked_reason:
            raise HTTPException(status_code=422, detail=blocked_reason)
        try:
            result = await service.suggest_selection(lead, tag_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"AI selection failed: {exc}") from exc
        return {"status": "ready", "job_id": job_id, **result.model_dump()}

    @router.post("/leads/{job_id}/pipeline/run")
    async def run_pipeline(
        job_id: str,
        bt: BackgroundTasks,
        repo: Repository = Depends(get_repository),
        job_store=Depends(get_job_runner),
    ):
        require_rate_limit(generate_limiter)
        lead = await asyncio.to_thread(repo.leads.get_lead_by_id, job_id)
        if not lead:
            raise HTTPException(status_code=404, detail="lead not found")
        job = job_store.create("pipeline_run", {"job_id": job_id})

        async def _run():
            from graph import PipelineState, eval_graph

            job_store.update(job.job_id, status="running", progress=10)
            try:
                profile = await asyncio.wait_for(asyncio.to_thread(repo.profile.get_profile), timeout=20)
            except Exception as log_exc:
                logging.getLogger(__name__).warning('suppressed exception in _run: %s', log_exc)
                profile = {}
            try:
                cfg = await asyncio.wait_for(asyncio.to_thread(repo.settings.get_settings), timeout=10)
            except Exception as log_exc:
                logging.getLogger(__name__).warning('suppressed exception in _run: %s', log_exc)
                cfg = {}
            state: PipelineState = {
                "job_id": job_id,
                "lead": lead,
                "profile": profile,
                "cfg": cfg,
                "score": 0,
                "reason": "",
                "match_points": [],
                "gaps": [],
                "asset_path": "",
                "cover_letter_path": "",
                "error": None,
            }
            try:
                result = await asyncio.to_thread(eval_graph.invoke, state)
                final_status = "failed" if result["error"] else "succeeded"
                job_store.update(job.job_id, status=final_status, progress=100, result={"score": result["score"], "error": result["error"]}, error=str(result["error"] or ""))
                await manager.broadcast({
                    "type": "agent",
                    "kind": "agent",
                    "src": "pipeline",
                    "event": "pipeline_done",
                    "msg": f"Pipeline done for {job_id}: score={result['score']}, error={result['error']}",
                })
            except Exception as exc:
                logging.getLogger(__name__).warning('suppressed exception in _run: %s', exc)
                job_store.update(job.job_id, status="failed", error=str(exc))
                await manager.broadcast({
                    "type": "agent",
                    "kind": "agent",
                    "src": "pipeline",
                    "event": "pipeline_done",
                    "msg": f"Pipeline failed for {job_id}: {exc}",
                })

        bt.add_task(_run)
        return {"status": "started", "job_id": job_id, "pipeline_job_id": job.job_id}

    return router
