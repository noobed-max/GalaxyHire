from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from api.dependencies import get_profile_service, get_repository
from api.rate_limit import RateLimiter, require_rate_limit
from core.paths import app_data_path

MAX_MISC_BYTES = 10 * 1024 * 1024
ALLOWED_SUFFIXES = {".pdf", ".doc", ".docx", ".txt", ".md"}


def create_router(logger) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["misc-context"])
    upload_limiter = RateLimiter(10, 60)
    repo = get_repository()

    @router.get("/misc-context")
    async def get_misc_context() -> dict:
        """The singleton misc record (merged text + when it was last updated)."""
        return repo.misc.get_misc()

    @router.post("/misc-context")
    async def update_misc_context(
        text: str = Form(""),
        file: UploadFile | None = File(None),
    ) -> dict:
        """Merge new facts into the singleton record (update, not append).

        Accepts pasted text, a file, or both. The LLM folds the incoming data into the
        previous record and only the merged result is stored. Failures surface as
        ingest_error with the previous record untouched — mirroring resume uploads.
        """
        require_rate_limit(upload_limiter)
        path: str | None = None
        if file is not None and (file.filename or ""):
            suffix = Path(file.filename or "").suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise HTTPException(400, f"Unsupported file type {suffix or '(none)'}")
            target_dir = app_data_path("documents", "misc")
            target_dir.mkdir(parents=True, exist_ok=True)
            import uuid as _uuid

            target = target_dir / f"{_uuid.uuid4().hex[:12]}{suffix}"
            total = 0
            with target.open("wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_MISC_BYTES:
                        out.close()
                        target.unlink(missing_ok=True)
                        raise HTTPException(413, "File too large (max 10 MB)")
                    out.write(chunk)
            path = str(target)
        if not (text or "").strip() and path is None:
            raise HTTPException(400, "paste text or upload a file first")
        try:
            merged = await get_profile_service().ingest_misc(raw=text, file_path=path)
            return {**merged, "ingest_error": ""}
        except Exception as exc:
            logger.warning("misc ingest failed: %s", exc)
            current = repo.misc.get_misc()
            return {**current, "ingest_error": str(exc).splitlines()[0][:300]}

    @router.delete("/misc-context")
    async def clear_misc_context() -> dict:
        repo.misc.clear_misc()
        return {"ok": True, "text": "", "updated_at": ""}

    return router
