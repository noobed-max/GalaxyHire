from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from importlib import import_module

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import Field

from api.dependencies import get_profile_service, get_ingestion_task_manager
from api.ingestion_tasks import IngestionTaskManager
from api.rate_limit import RateLimiter, require_rate_limit
from core.paths import app_data_path
from core.types import StrictBody
from data.repository import create_repository

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
ALLOWED_SUFFIXES = {".pdf", ".doc", ".docx", ".txt", ".md"}
MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


class DocumentPatchBody(StrictBody):
    topic: str | None = Field(default=None, max_length=120)
    tag_id: str | None = Field(default=None, max_length=32)


class TagCreateBody(StrictBody):
    name: str = Field(min_length=1, max_length=60)


class PointTagsBody(StrictBody):
    # Full replacement set for one profile point (chip-toggle UI sends all).
    tag_ids: list[str] = Field(default_factory=list, max_length=64)


class ReconcilePointTagsBody(StrictBody):
    document_id: str | None = Field(default=None, max_length=64)
    tag_id: str | None = Field(default=None, max_length=64)


class DuplicateResolutionItem(StrictBody):
    pair_id: str = Field(min_length=1)
    action: str = Field(...)


class DuplicateResolveBody(StrictBody):
    resolutions: list[DuplicateResolutionItem] = Field(default_factory=list)


def _excerpt_for(path: str) -> str:
    """Plain-text excerpt used for previews and as the cover-letter rewrite base."""
    suffix = Path(path).suffix.lower()
    if suffix not in {".txt", ".md", ".pdf", ".docx"}:
        return ""
    try:
        # Lazy import keeps the api->profile boundary (test_import_boundaries).
        _document = import_module("profile.ingest_documents")._document
        return (_document(path) or "")[:8000]
    except Exception:
        return ""


def _save_upload(file: UploadFile, kind: str) -> tuple[str, str]:
    """Persist the upload under the app data dir; returns (path, mime)."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type {suffix or '(none)'}; allowed: {', '.join(sorted(ALLOWED_SUFFIXES))}")
    target_dir = app_data_path("documents", kind)
    target_dir.mkdir(parents=True, exist_ok=True)

    target = target_dir / f"{uuid.uuid4().hex[:12]}{suffix}"
    total = 0
    with target.open("wb") as out:
        while chunk := file.file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_DOCUMENT_BYTES:
                out.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, f"File too large (max {MAX_DOCUMENT_BYTES // 1024 // 1024} MB)")
            out.write(chunk)
    return str(target), MIME_BY_SUFFIX.get(suffix, "application/octet-stream")


async def _background_ingest_worker(
    *,
    task_id: str,
    path: str,
    raw: str,
    kind: str,
    tag_id: str,
    topic: str,
    source_filename: str,
    mime: str,
    task_manager: IngestionTaskManager,
    logger,
    connection_manager=None,
) -> None:
    repo = create_repository()
    profile_service = get_profile_service()
    try:
        async def on_stage(stage: str, stage_number: int, message: str):
            await task_manager.update_task(
                task_id,
                stage=stage,
                stage_number=stage_number,
                stage_message=message,
                progress_percent=min(95, stage_number * 25),
            )
            if connection_manager:
                try:
                    await connection_manager.broadcast({
                        "type": "ingest_progress",
                        "task_id": task_id,
                        "stage": stage,
                        "stage_number": stage_number,
                        "message": message,
                        "progress_percent": min(95, stage_number * 25),
                    })
                except Exception:
                    pass

        async def on_thought(thought: str):
            await task_manager.append_thought(task_id, thought)
            if connection_manager:
                try:
                    await connection_manager.broadcast({
                        "type": "ingest_thought",
                        "task_id": task_id,
                        "thought": thought,
                    })
                except Exception:
                    pass

        if kind == "cover_letter":
            await on_stage("reading", 1, "Reading cover letter...")
            excerpt = await asyncio.to_thread(_excerpt_for, path) if path else raw[:8000]
            doc = repo.documents.create_document(
                kind=kind,
                file_path=path,
                topic=topic or Path(source_filename).stem,
                tag_id=tag_id or None,
                source_filename=source_filename,
                mime=mime,
                excerpt=excerpt,
                source="upload",
            )
            await on_thought(f"Cover letter registered successfully with ID {doc['id']}.")
            await task_manager.update_task(
                task_id,
                status="completed",
                stage="completed",
                stage_number=4,
                stage_message="Cover letter saved successfully.",
                progress_percent=100,
                completed_at=datetime.now(timezone.utc).isoformat(),
                result={"document_id": doc["id"]},
            )
            return

        # Resume Ingestion (All 4 stages)
        profile = await profile_service.ingest_resume(
            raw=raw,
            pdf_path=path if path else None,
            tag_id=tag_id or None,
            resume_id=task_id,
            on_stage=on_stage,
            on_thought=on_thought,
        )

        excerpt = await asyncio.to_thread(_excerpt_for, path) if path else raw[:8000]
        doc = repo.documents.create_document(
            kind="resume",
            file_path=path,
            topic=topic or Path(source_filename).stem,
            tag_id=tag_id or None,
            source_filename=source_filename,
            mime=mime,
            excerpt=excerpt,
            source="upload",
            resume_id=task_id,
            parsed_profile=profile.model_dump() if hasattr(profile, "model_dump") else profile,
        )
        await on_thought(f"Document registered in library with ID {doc['id']}.")

        # The ingest path already links parsed canonical points.  This
        # idempotent reconciliation also covers legacy parsers/partial rows and
        # gives the task result an auditable count instead of claiming a tag was
        # complete when no point rows were written.
        tag_repair = await asyncio.to_thread(
            repo.point_tags.reconcile_tagged_resume_document,
            doc["id"],
        )

        skill_count = len(getattr(profile, "skills", []) or (profile.get("skills", []) if isinstance(profile, dict) else []))
        exp_count = len(getattr(profile, "exp", []) or (profile.get("exp", []) if isinstance(profile, dict) else []))
        proj_count = len(getattr(profile, "projects", []) or (profile.get("projects", []) if isinstance(profile, dict) else []))

        result_payload = {
            "document_id": doc["id"],
            "skills_count": skill_count,
            "roles_count": exp_count,
            "projects_count": proj_count,
            "tag_repair": tag_repair,
        }

        # Collect staged duplicate pairs from parsed profile
        raw_exp = getattr(profile, "exp", []) or (profile.get("exp", []) if isinstance(profile, dict) else [])
        raw_projects = getattr(profile, "projects", []) or (profile.get("projects", []) if isinstance(profile, dict) else [])
        from data.graph import profile as graph_profile

        staged_items = []
        for e in raw_exp:
            e_dict = e if isinstance(e, dict) else (e.model_dump() if hasattr(e, "model_dump") else {})
            sim_pairs = e_dict.get("similar_pairs") or []
            if not sim_pairs:
                continue
            role = str(e_dict.get("role") or "").strip()
            co = str(e_dict.get("company") or e_dict.get("co") or "").strip()
            period = str(e_dict.get("period") or "").strip()
            parent_id = str(
                e_dict.get("matched_entity_id")
                or e_dict.get("id")
                or graph_profile.hash_id(role + co)
            )
            entity_title = f"{co} ({role} · {period})" if (role and period) else (f"{co} ({role})" if role else co)
            for pair in sim_pairs:
                p_dict = pair if isinstance(pair, dict) else (pair.model_dump() if hasattr(pair, "model_dump") else {})
                staged_items.append({
                    "id": f"dup_{uuid.uuid4().hex[:12]}",
                    "task_id": task_id,
                    "document_id": doc["id"],
                    "tag_id": tag_id or None,
                    "parent_kind": "experience",
                    "parent_id": parent_id,
                    "company_name": co,
                    "role": role,
                    "period": period,
                    "entity_title": entity_title,
                    "existing_point_id": str(p_dict.get("existing_point_id") or ""),
                    "existing_text": str(p_dict.get("existing_text") or ""),
                    "new_text": str(p_dict.get("new_text") or ""),
                    "explanation": str(p_dict.get("explanation") or ""),
                    "status": "pending",
                })

        for p in raw_projects:
            p_dict = p if isinstance(p, dict) else (p.model_dump() if hasattr(p, "model_dump") else {})
            sim_pairs = p_dict.get("similar_pairs") or []
            if not sim_pairs:
                continue
            title = str(p_dict.get("title") or "").strip()
            parent_id = str(
                p_dict.get("matched_entity_id")
                or p_dict.get("id")
                or graph_profile.hash_id(title)
            )
            entity_title = title
            for pair in sim_pairs:
                p_dict2 = pair if isinstance(pair, dict) else (pair.model_dump() if hasattr(pair, "model_dump") else {})
                staged_items.append({
                    "id": f"dup_{uuid.uuid4().hex[:12]}",
                    "task_id": task_id,
                    "document_id": doc["id"],
                    "tag_id": tag_id or None,
                    "parent_kind": "project",
                    "parent_id": parent_id,
                    "company_name": title,
                    "role": "",
                    "period": "",
                    "entity_title": entity_title,
                    "existing_point_id": str(p_dict2.get("existing_point_id") or ""),
                    "existing_text": str(p_dict2.get("existing_text") or ""),
                    "new_text": str(p_dict2.get("new_text") or ""),
                    "explanation": str(p_dict2.get("explanation") or ""),
                    "status": "pending",
                })

        if staged_items:
            await asyncio.to_thread(repo.ingestion_tasks.create_staged_duplicates_batch, staged_items)
            duplicate_groups = await asyncio.to_thread(repo.ingestion_tasks.get_staged_duplicates_grouped, task_id)
            await task_manager.update_task(
                task_id,
                status="review_required",
                stage="completed",
                stage_number=4,
                stage_message=f"Ingestion complete. {len(staged_items)} similar bullets staged for review.",
                progress_percent=100,
                completed_at=datetime.now(timezone.utc).isoformat(),
                duplicates=duplicate_groups,
                result=result_payload,
            )
            await on_thought(f"{len(staged_items)} similar bullet point(s) staged for duplicate review.")
            if connection_manager:
                try:
                    await connection_manager.broadcast({
                        "type": "ingest_review_required",
                        "task_id": task_id,
                        "duplicates_count": len(staged_items),
                    })
                except Exception:
                    pass
        else:
            await task_manager.update_task(
                task_id,
                status="completed",
                stage="completed",
                stage_number=4,
                stage_message="Ingestion completed successfully.",
                progress_percent=100,
                completed_at=datetime.now(timezone.utc).isoformat(),
                result=result_payload,
            )
            await on_thought("Ingestion complete. Profile updated.")

        if connection_manager:
            try:
                await connection_manager.broadcast({
                    "type": "agent",
                    "event": "ingested",
                    "task_id": task_id,
                    "msg": f"Profile ingested: {source_filename} — {skill_count} skills, {exp_count} roles",
                })
            except Exception:
                pass

    except asyncio.CancelledError:
        logger.info("Background ingestion cancelled for task %s", task_id)
        raise
    except Exception as exc:
        logger.error("Background ingestion failed for task %s: %s", task_id, exc, exc_info=True)
        doc_id = None
        if path and os.path.exists(path):
            try:
                excerpt = await asyncio.to_thread(_excerpt_for, path)
                doc = repo.documents.create_document(
                    kind=kind,
                    file_path=path,
                    topic=topic or Path(source_filename).stem,
                    tag_id=tag_id or None,
                    source_filename=source_filename,
                    mime=mime,
                    excerpt=excerpt,
                    source="upload",
                )
                doc_id = doc["id"]
            except Exception:
                pass

        error_msg = str(exc).splitlines()[0][:300] if str(exc) else "Ingestion failed"
        await task_manager.append_thought(task_id, f"Error: {error_msg}")
        await task_manager.update_task(
            task_id,
            status="failed",
            stage="failed",
            stage_message="Ingestion failed.",
            error=error_msg,
            completed_at=datetime.now(timezone.utc).isoformat(),
            result={"document_id": doc_id} if doc_id else {},
        )
        if connection_manager:
            try:
                await connection_manager.broadcast({
                    "type": "ingest_failed",
                    "task_id": task_id,
                    "error": error_msg,
                })
            except Exception:
                pass


def create_router(logger, connection_manager=None) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["documents"])
    upload_limiter = RateLimiter(10, 60)
    repo = create_repository()

    # ── tags ────────────────────────────────────────────────────────────────
    @router.get("/tags")
    async def list_tags() -> dict:
        return {"tags": repo.tags.list_tags()}

    @router.post("/tags")
    async def create_tag(body: TagCreateBody) -> dict:
        try:
            return repo.tags.create_tag(body.name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete("/tags/{tag_id}")
    async def delete_tag(tag_id: str) -> dict:
        # Documents keep their files; they just become untagged (SET NULL).
        ok = repo.tags.delete_tag(tag_id)
        if not ok:
            raise HTTPException(404, "tag not found")
        repo.point_tags.delete_for_tag(tag_id)
        return {"ok": True}

    # ── profile-point tags ──────────────────────────────────────────────────
    @router.get("/point-tags")
    async def list_point_tags() -> dict:
        return {"point_tags": repo.point_tags.list_point_tags()}

    @router.put("/point-tags/{kind}/{point_id}")
    async def set_point_tags(kind: str, point_id: str, body: PointTagsBody) -> dict:
        try:
            rows = await asyncio.to_thread(
                repo.point_tags.set_point_tags, kind, point_id, body.tag_ids
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"point_tags": rows}

    @router.post("/point-tags/reconcile")
    @router.post("/documents/reconcile-point-tags")
    async def reconcile_point_tags(
        document_id: str | None = None,
        tag_id: str | None = None,
        body: ReconcilePointTagsBody | None = None,
    ) -> dict:
        """Repair tagged resume provenance from stored excerpts, without an LLM."""
        document_id = document_id or (body.document_id if body else None)
        tag_id = tag_id or (body.tag_id if body else None)
        return await asyncio.to_thread(
            repo.point_tags.reconcile_tagged_resume_documents,
            document_id=document_id,
            tag_id=tag_id,
        )

    # ── documents ───────────────────────────────────────────────────────────
    @router.get("/documents")
    async def list_documents(kind: str | None = None, tag_id: str | None = None) -> dict:
        if kind not in {None, "resume", "cover_letter"}:
            raise HTTPException(400, "kind must be 'resume' or 'cover_letter'")
        docs = repo.documents.list_documents(kind=kind, tag_id=tag_id)
        tags = {t["id"]: t["name"] for t in repo.tags.list_tags()}
        for d in docs:
            d["tag_name"] = tags.get(d.get("tag_id") or "", "")
        return {"documents": docs}

    @router.post("/documents")
    async def upload_document(
        kind: str = Form(...),
        file: UploadFile = File(...),
        topic: str = Form(""),
        tag_id: str = Form(""),
        ingest: str = Form("true"),
    ) -> dict:
        require_rate_limit(upload_limiter)
        if kind not in {"resume", "cover_letter"}:
            raise HTTPException(400, "kind must be 'resume' or 'cover_letter'")
        path, mime = _save_upload(file, kind)
        ingest_error = ""
        try:
            # Resumes still feed the profile graph exactly like /ingest did —
            # the document registry is an additional layer, not a replacement.
            # Extraction failure must not lose the upload, but it MUST surface:
            # a silent empty profile looks identical to success in the UI.
            parsed_profile = None
            resume_id = f"resume_{uuid.uuid4().hex}"
            if kind == "resume" and ingest == "true":
                try:
                    parsed_profile = await get_profile_service().ingest_resume(
                        pdf_path=path,
                        tag_id=tag_id or None,
                        resume_id=resume_id,
                    )
                except Exception as exc:
                    logger.warning("resume ingest after upload failed: %s", exc)
                    ingest_error = str(exc).splitlines()[0][:300]
            excerpt = await asyncio.to_thread(_excerpt_for, path)
            doc = repo.documents.create_document(
                kind=kind,
                file_path=path,
                topic=topic or Path(file.filename or "").stem,
                tag_id=tag_id or None,
                source_filename=file.filename or "",
                mime=mime,
                excerpt=excerpt,
                source="upload",
                resume_id=resume_id if kind == "resume" else "",
                parsed_profile=(
                    parsed_profile.model_dump()
                    if hasattr(parsed_profile, "model_dump")
                    else parsed_profile
                ),
            )
            if kind == "resume" and tag_id:
                doc["tag_repair"] = await asyncio.to_thread(
                    repo.point_tags.reconcile_tagged_resume_document,
                    doc["id"],
                )
            if ingest_error:
                doc["ingest_error"] = ingest_error
            return doc
        except HTTPException:
            raise
        except Exception as exc:
            Path(path).unlink(missing_ok=True)
            raise HTTPException(500, f"could not register document: {exc}") from exc

    # ── async ingestion pipeline ─────────────────────────────────────────────
    @router.post("/documents/ingest", status_code=status.HTTP_202_ACCEPTED)
    async def ingest_document(
        request: Request,
        file: UploadFile | None = File(None),
        raw: str = Form(""),
        kind: str = Form("resume"),
        tag_id: str = Form(""),
        topic: str = Form(""),
    ) -> dict:
        require_rate_limit(upload_limiter)
        if kind not in {"resume", "cover_letter"}:
            raise HTTPException(400, "kind must be 'resume' or 'cover_letter'")

        has_file = file is not None and bool(file.filename and file.filename.strip())
        has_raw = bool(raw and raw.strip())
        if not has_file and not has_raw:
            raise HTTPException(400, "Either 'file' or 'raw' text must be provided")

        task_mgr = get_ingestion_task_manager()
        active = await task_mgr.get_active_or_latest_task()
        if active and active.status == "processing":
            raise HTTPException(
                status_code=409,
                detail=f"Another resume ingestion task is already in progress ({active.task_id}).",
            )

        if has_file and file is not None:
            path, mime = _save_upload(file, kind)
            filename = file.filename or ""
        else:
            target_dir = app_data_path("documents", kind)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{uuid.uuid4().hex[:12]}.txt"
            target.write_text(raw, encoding="utf-8")
            path = str(target)
            mime = "text/plain"
            filename = topic or "pasted_text.txt"

        task = await task_mgr.create_task(
            filename=filename,
            file_path=path,
            tag_id=tag_id or None,
            topic=topic,
        )

        cm = getattr(request.app.state, "connection_manager", None) or connection_manager
        task_mgr.spawn_worker(
            task.task_id,
            _background_ingest_worker(
                task_id=task.task_id,
                path=path,
                raw=raw,
                kind=kind,
                tag_id=tag_id,
                topic=topic,
                source_filename=filename,
                mime=mime,
                task_manager=task_mgr,
                logger=logger,
                connection_manager=cm,
            ),
        )

        return {
            "task_id": task.task_id,
            "status": "processing",
            "stage": task.stage,
            "filename": task.filename,
            "tag_id": task.tag_id,
            "started_at": task.started_at,
        }

    @router.get("/documents/ingest/status")
    async def get_ingest_status(task_id: str | None = None) -> dict:
        task_mgr = get_ingestion_task_manager()
        if task_id:
            task = await task_mgr.get_task(task_id)
            if not task:
                raise HTTPException(404, f"Ingestion task '{task_id}' not found")
            return task.to_dict()

        task = await task_mgr.get_active_or_latest_task()
        if task:
            return task.to_dict()

        return {
            "task_id": None,
            "status": "idle",
            "stage": "idle",
            "stage_number": 0,
            "stage_message": "No ingestion task active.",
            "filename": None,
            "tag_id": None,
            "elapsed_seconds": 0.0,
            "progress_percent": 0,
            "thoughts": [],
            "has_staged_duplicates": False,
            "reviewable_duplicates": [],
            "error": None,
            "started_at": None,
            "completed_at": None,
            "result": None,
        }

    @router.post("/documents/ingest/{task_id}/cancel")
    async def cancel_ingest(task_id: str) -> dict:
        task_mgr = get_ingestion_task_manager()
        task = await task_mgr.get_task(task_id)
        if not task:
            raise HTTPException(404, f"Ingestion task '{task_id}' not found")
        ok = await task_mgr.cancel_task(task_id)
        return {"task_id": task_id, "status": "cancelled", "ok": ok}

    @router.get("/documents/ingest/{task_id}/duplicates")
    async def get_ingest_duplicates(task_id: str) -> dict:
        task_mgr = get_ingestion_task_manager()
        task = await task_mgr.get_task(task_id)
        if not task:
            raise HTTPException(404, f"Ingestion task '{task_id}' not found")
        groups = await asyncio.to_thread(repo.ingestion_tasks.get_staged_duplicates_grouped, task_id)
        return {
            "task_id": task_id,
            "status": task.status,
            "groups": groups,
        }

    @router.post("/documents/ingest/{task_id}/resolve")
    async def resolve_ingest_duplicates(task_id: str, body: DuplicateResolveBody) -> dict:
        task_mgr = get_ingestion_task_manager()
        task = await task_mgr.get_task(task_id)
        if not task:
            raise HTTPException(404, f"Ingestion task '{task_id}' not found")

        valid_actions = {"use_new", "keep_both", "keep_existing"}
        for item in body.resolutions:
            if item.action not in valid_actions:
                raise HTTPException(422, f"Invalid resolution action '{item.action}'; allowed: {', '.join(sorted(valid_actions))}")

        profile_svc = get_profile_service()
        resolved_count = 0
        for item in body.resolutions:
            dup = await asyncio.to_thread(repo.ingestion_tasks.get_staged_duplicate, item.pair_id)
            if not dup:
                continue
            if dup.get("task_id") != task_id:
                raise HTTPException(400, f"Duplicate pair '{item.pair_id}' does not belong to task '{task_id}'")

            await profile_svc.resolve_duplicate_action(dup, item.action, tag_id=task.tag_id)
            await asyncio.to_thread(
                repo.ingestion_tasks.update_staged_duplicate_status,
                item.pair_id,
                f"resolved_{item.action}",
            )
            resolved_count += 1

        remaining = await asyncio.to_thread(repo.ingestion_tasks.count_pending_staged_duplicates, task_id)
        if remaining == 0:
            await task_mgr.update_task(task_id, status="completed", duplicates=[])
            if connection_manager:
                try:
                    await connection_manager.broadcast({"type": "ingest_resolved", "task_id": task_id})
                except Exception:
                    pass

        return {
            "status": "resolved",
            "task_id": task_id,
            "resolved_count": resolved_count,
            "remaining_count": remaining,
        }

    @router.patch("/documents/{doc_id}")
    async def patch_document(doc_id: str, body: DocumentPatchBody) -> dict:
        doc = repo.documents.update_document(doc_id, topic=body.topic, tag_id=body.tag_id)
        if not doc:
            raise HTTPException(404, "document not found")
        if doc.get("kind") == "resume" and doc.get("tag_id"):
            doc["tag_repair"] = await asyncio.to_thread(
                repo.point_tags.reconcile_tagged_resume_document,
                doc["id"],
            )
        # Enrich like the list endpoint so callers get the resolved tag name
        # without a refetch.
        tags = {t["id"]: t["name"] for t in repo.tags.list_tags()}
        doc["tag_name"] = tags.get(doc.get("tag_id") or "", "")
        return doc

    @router.delete("/documents/{doc_id}")
    async def delete_document(doc_id: str) -> dict:
        """Deletes this file + registry row only. Shared profile points and
        other documents (even in the same tag) are untouched."""
        doc = repo.documents.delete_document(doc_id)
        if not doc:
            raise HTTPException(404, "document not found")
        try:
            os.unlink(doc["file_path"])
        except OSError:
            pass
        return {"ok": True, "deleted": doc}

    @router.get("/documents/{doc_id}/file")
    async def document_file(doc_id: str):
        doc = repo.documents.get_document(doc_id)
        if not doc:
            raise HTTPException(404, "document not found")
        if not os.path.isfile(doc["file_path"]):
            raise HTTPException(410, "file is gone from disk")
        return FileResponse(
            doc["file_path"],
            media_type=doc.get("mime") or "application/octet-stream",
            filename=doc.get("source_filename") or Path(doc["file_path"]).name,
        )

    return router
