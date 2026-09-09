"""Profile endpoints (docs/04 §1, docs/07 §3). API-key guarded.

v1 is single-user-per-client: the user is identified by an `x-user-id` header, defaulting to a
fixed dev user so the extension works without extra setup.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel

from galaxy.api.deps import require_api_key
from galaxy.api.throttle import throttle
from galaxy.models.profile import Profile
from galaxy.search.profiles import ProfileStore

router = APIRouter(
    prefix="/profile", tags=["profile"],
    dependencies=[Depends(require_api_key), Depends(throttle)],
)

DEV_USER = UUID("00000000-0000-0000-0000-000000000001")


def _user(x_user_id: str | None = Header(default=None)) -> UUID:
    if not x_user_id:
        return DEV_USER
    try:
        return UUID(x_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid x-user-id") from exc


@router.get("", response_model=Profile | None)
async def get_profile(user_id: UUID = Depends(_user)) -> Profile | None:
    return await ProfileStore().get(user_id)


class ImportRequest(BaseModel):
    resume_text: str


@router.post("/import", response_model=Profile)
async def import_profile(req: ImportRequest, user_id: UUID = Depends(_user)) -> Profile:
    """Parse pasted resume text into a Profile DRAFT (not saved — user reviews then PUTs it)."""
    from galaxy.search.profile_import import import_from_text

    if not req.resume_text.strip():
        raise HTTPException(status_code=400, detail="resume_text is empty")
    try:
        return await import_from_text(user_id, req.resume_text)
    except Exception as exc:  # noqa: BLE001 — LLM/endpoint failures surface as a clean 502
        raise HTTPException(status_code=502, detail=f"profile import failed: {exc}") from exc


@router.post("/import-file", response_model=Profile)
async def import_profile_file(
    file: UploadFile = File(...), user_id: UUID = Depends(_user)
) -> Profile:
    """Upload a resume (.pdf/.docx/.txt/.md) → extract text → LLM parse into a Profile DRAFT (docs/11)."""
    from galaxy.search.profile_import import extract_resume_text, import_from_text

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    text = extract_resume_text(file.filename or "", data)
    if not text.strip():
        raise HTTPException(status_code=422, detail="could not extract text from the file")
    try:
        return await import_from_text(user_id, text)
    except Exception as exc:  # noqa: BLE001 — LLM/endpoint failures surface as a clean 502
        raise HTTPException(status_code=502, detail=f"profile import failed: {exc}") from exc


@router.put("", response_model=Profile)
async def put_profile(profile: Profile, user_id: UUID = Depends(_user)) -> Profile:
    # the path user wins over any user_id in the body
    profile.user_id = user_id
    saved = await ProfileStore().upsert(profile)
    # a profile edit changes ranking/blacklist → drop cached searches so it takes effect now
    from galaxy.common.cache import search_cache

    search_cache.clear()
    return saved
