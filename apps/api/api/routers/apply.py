"""The apply flow (§6) — prepare, hand off to the extension, record what happened.

Sequence, and the reason it's split this way:

    POST /leads/{id}/apply        tailor, write the résumé to the user's folder, return apply_url
                                  + the fill context. Records "apply_opened".
    GET  /fill-context?url=...    the extension reads this for the active tab
    POST /leads/{id}/filled       the extension reports which fields it filled
    POST /leads/{id}/applied      the human confirms they submitted

**Opening a form is not applying.** Preparing and marking "applied" are separate endpoints because
conflating them would fill the pipeline with applications that were never submitted — the user
opens a portal, decides against it, and the dashboard would still claim they applied. Only the last
call sets that status, and only a human can trigger it, which is the same invariant the extension
enforces at the DOM level (it fills; it never submits).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.dependencies import get_repository
from core.logging import get_logger
from corpus.client import CorpusUnavailable, create_corpus_client

_log = get_logger(__name__)

# Where résumés land when the user hasn't chosen a folder. Read from settings so the browser shell
# can set it by typing a path — browsers cannot hand out filesystem paths from a picker (see the
# `pickDirectory` note in the web platform layer).
RESUME_DIR_SETTING = "resume_output_dir"


def _text(value) -> str:
    return str(value or "").strip()


def _list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    raw = _text(value)
    return [part.strip() for part in raw.split(",") if part.strip()] if raw else []


def _bullets(value) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    return [
        line.strip(" \t-*•")
        for line in _text(value).splitlines()
        if line.strip(" \t-*•")
    ]


def _manual_fill_context(repo, lead: dict) -> dict:
    """Build the extension payload for a locally-created/manual job.

    Manual leads and generated files live in the gateway, not the corpus. The old
    handoff sent their local IDs to Postgres and therefore could never succeed.
    """
    resume_path = _text(lead.get("resume_asset") or lead.get("asset"))
    if not resume_path or not Path(resume_path).is_file():
        raise HTTPException(status_code=409, detail="the tailored résumé is not ready yet")

    profile = repo.profile.get_profile() or {}
    identity = profile.get("identity") if isinstance(profile.get("identity"), dict) else {}
    links = [
        _text(identity.get(key) or profile.get(key))
        for key in ("linkedin_url", "github_url", "website_url")
        if _text(identity.get(key) or profile.get(key))
    ]
    city = _text(identity.get("city") or profile.get("city"))

    skills = [
        _text(item.get("n") or item.get("name") or item.get("title")) if isinstance(item, dict) else _text(item)
        for item in _list(profile.get("skills"))
    ]
    skills = list(dict.fromkeys(item for item in skills if item))

    selected = {_text(item).lower() for item in _list(lead.get("selected_projects")) if _text(item)}
    projects: list[dict] = []
    for item in _list(profile.get("projects")):
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title") or item.get("name"))
        project_id = _text(item.get("id"))
        if selected and title.lower() not in selected and project_id.lower() not in selected:
            continue
        stack = _list(
            item.get("stack") or item.get("skills") or item.get("technologies") or item.get("tech_stack")
        )
        bullets = _bullets(item.get("bullets") or item.get("impact") or item.get("description"))
        projects.append({"title": title, "bullets": bullets, "skills": [_text(v) for v in stack if _text(v)]})

    experience: list[dict] = []
    for item in _list(profile.get("exp") or profile.get("experience")):
        if not isinstance(item, dict):
            continue
        experience.append(
            {
                "title": _text(item.get("role") or item.get("title")),
                "company": _text(item.get("co") or item.get("company")),
                "dates": _text(item.get("period") or item.get("dates")),
                "bullets": _bullets(item.get("bullets") or item.get("d") or item.get("description")),
            }
        )

    job_id = _text(lead.get("job_id"))
    # Misc user data (visa, citizenship, military, EEO answers, ...) rides along as extra
    # facts for the extension LLM. It is advisory and global — never tag-scoped.
    misc_text = ""
    try:
        misc_text = _text((repo.misc.get_misc() or {}).get("text"))
    except Exception:
        misc_text = ""
    anything_base = _text(profile.get("s") or profile.get("anything_else"))
    anything_else = f"{anything_base}\n\nMISCELLANEOUS USER DATA:\n{misc_text}".strip() if misc_text else anything_base
    return {
        "job_id": job_id,
        "company": _text(lead.get("company")),
        "title": _text(lead.get("title")),
        "apply_url": _text(lead.get("url")),
        "identity": {
            "name": _text(profile.get("n") or identity.get("name")),
            "email": _text(identity.get("email") or profile.get("email")),
            "phone": _text(identity.get("phone") or profile.get("phone")),
            "links": links,
            "locations": [city] if city else [],
            "citizenship": _text(identity.get("citizenship")) or None,
            "work_authorization": _text(identity.get("work_authorization")) or None,
            "willing_to_relocate": identity.get("willing_to_relocate"),
        },
        "anything_else": anything_else,
        "skills": skills,
        "projects": projects,
        "experience": experience,
        "resume_ref": "",
        "resume_url": f"/api/v1/leads/{job_id}/pdf",
        "resume_path": resume_path,
        "resume_paths": {"pdf": resume_path},
        "ats_score": float(lead.get("score") or 0),
        "prepared_at": datetime.now(UTC).isoformat(),
    }


def _url_host(value: str | None) -> str:
    try:
        return (urlparse(value or "").hostname or "").lower()
    except ValueError:
        return ""


def _local_fill_context(repo, url: str | None) -> dict | None:
    """Find a persisted manual handoff for the active tab, newest first."""
    if not hasattr(repo.leads, "get_all_leads"):
        return None
    requested_host = _url_host(url)
    candidates: list[dict] = []
    for lead in repo.leads.get_all_leads():
        meta = lead.get("source_meta") if isinstance(lead.get("source_meta"), dict) else {}
        context = meta.get("prepared_fill_context")
        if not isinstance(context, dict):
            continue
        if requested_host and _url_host(context.get("apply_url")) != requested_host:
            continue
        candidates.append(context)
    if not candidates:
        return None
    return max(candidates, key=lambda item: _text(item.get("prepared_at")))


class ApplyRequest(BaseModel):
    # Skipping the liveness check is occasionally wanted: a posting that 404s to us may still be
    # open, and the user has already decided to apply.
    check_live: bool = True


class FilledRequest(BaseModel):
    """What the extension actually managed to fill."""

    filled: list[str] = []
    skipped: list[str] = []
    url: str | None = None


class FillFieldItem(BaseModel):
    """One scanned form field as the page sees it — labels/options come from the DOM,
    the backend never touches the page itself."""

    id: int
    label: str = ""
    kind: str = "text"
    type: str = "text"
    options: list[str] = Field(default_factory=list, max_length=40)
    required: bool = False


class FillDecideRequest(BaseModel):
    job_id: str = ""
    fields: list[FillFieldItem] = Field(default_factory=list, max_length=120)


class FillDecisionLine(BaseModel):
    id: int
    value: str = ""


class FillDecision(BaseModel):
    """Structured-output shape for the fill-decision LLM call."""

    fills: list[FillDecisionLine] = Field(default_factory=list)


_FILL_SYSTEM = (
    "You fill job application forms from a candidate profile. "
    "Rules: 1) Use ONLY facts from the profile — never invent names, dates, salaries or codes. "
    "2) Omit any field the profile cannot answer; do not guess. "
    "3) For select/radio/checkbox-group, copy an option EXACTLY as written. "
    "4) Match the field type: number fields take digits only, date fields take YYYY-MM-DD. "
    "5) ADDITIONAL NOTES is free-form prose the candidate wrote — mine it for visa/work-auth, "
    "EEO/demographic, clearance, notice period, relocation, salary. Treat it as fact. "
    "6) For EEO/demographic questions the notes decline or are silent on, choose the "
    '\"prefer not to say\" option when offered rather than omitting the field.'
)


def _fill_profile_prose(profile: dict, misc_text: str = "") -> str:
    """Render the stored profile into the fact block the fill model reads."""
    identity = profile.get("identity") if isinstance(profile.get("identity"), dict) else {}
    lines: list[str] = ["CANDIDATE PROFILE", ""]
    name = _text(profile.get("n") or identity.get("name"))
    email = _text(identity.get("email") or profile.get("email"))
    phone = _text(identity.get("phone") or profile.get("phone"))
    city = _text(identity.get("city") or profile.get("city"))
    if name:
        lines.append(f"Name: {name}")
    if email:
        lines.append(f"Email: {email}")
    if phone:
        lines.append(f"Phone: {phone}")
    if city:
        lines.append(f"Location: {city}")
    links = [
        _text(identity.get(key) or profile.get(key))
        for key in ("linkedin_url", "github_url", "website_url")
    ]
    links = [link for link in links if link]
    if links:
        lines.append(f"Links: {', '.join(links)}")
    for key, label in (("citizenship", "Citizenship"), ("work_authorization", "Work authorization")):
        value = _text(identity.get(key))
        if value:
            lines.append(f"{label}: {value}")
    relocate = identity.get("willing_to_relocate")
    if relocate is not None:
        lines.append(f"Willing to relocate: {'yes' if relocate else 'no'}")

    skills = [
        _text(item.get("n") or item.get("name")) if isinstance(item, dict) else _text(item)
        for item in _list(profile.get("skills"))
    ]
    skills = [item for item in skills if item]
    if skills:
        lines.extend(["", "SKILLS:", ", ".join(skills)])

    projects = []
    for item in _list(profile.get("projects")):
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title") or item.get("name"))
        bullets = _bullets(item.get("bullets") or item.get("impact") or item.get("description"))
        if title or bullets:
            projects.append((title, bullets))
    if projects:
        lines.append("")
        lines.append("PROJECTS:")
        for title, bullets in projects:
            lines.append(f"- {title}")
            lines.extend(f"  · {bullet}" for bullet in bullets)

    experiences = []
    for item in _list(profile.get("exp") or profile.get("experience")):
        if not isinstance(item, dict):
            continue
        head = " — ".join(
            part for part in (
                _text(item.get("role") or item.get("title")),
                _text(item.get("co") or item.get("company")),
                _text(item.get("period") or item.get("dates")),
            ) if part
        )
        bullets = _bullets(item.get("bullets") or item.get("d") or item.get("description"))
        if head or bullets:
            experiences.append((head, bullets))
    if experiences:
        lines.append("")
        lines.append("WORK EXPERIENCE:")
        for head, bullets in experiences:
            if head:
                lines.append(f"- {head}")
            lines.extend(f"  · {bullet}" for bullet in bullets)

    notes = _text(profile.get("s") or profile.get("anything_else"))
    if notes:
        lines.extend(["", "ADDITIONAL NOTES (free-form, treat as fact):", notes])
    misc_text = _text(misc_text)
    if misc_text:
        lines.extend(["", "MISCELLANEOUS USER DATA (candidate-supplied facts for forms — visa, citizenship,", "military, EEO answers, notice period. Treat as fact, never invent beyond it):", misc_text])
    return "\n".join(lines)


def create_router(manager=None) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["apply"])

    async def _broadcast(event: str, msg: str) -> None:
        if manager is not None:
            await manager.broadcast({"type": "agent", "event": event, "msg": msg})

    def _record(job_id: str, action: str) -> None:
        """Append to the activity log, never fatally.

        Losing an audit line must not fail the apply the user is in the middle of.
        """
        try:
            get_repository().leads.record_event(job_id, action)
        except Exception as exc:  # noqa: BLE001
            _log.warning("could not record %s for %s: %s", action, job_id, exc)

    @router.post("/leads/{job_id}/apply")
    async def apply(job_id: str, req: ApplyRequest | None = None) -> dict:
        """Prepare everything the user needs to apply, then hand off.

        Returns `apply_url` for the UI to open and the fill context the extension will use. Does
        **not** mark the lead applied — see the module docstring.
        """
        repo = get_repository()
        settings = repo.settings.get_settings()
        output_dir = (settings.get(RESUME_DIR_SETTING) or "").strip() or None
        local_lead = repo.leads.get_lead_by_id(job_id) if hasattr(repo.leads, "get_lead_by_id") else None

        has_local_tailored = bool(
            local_lead and (
                local_lead.get("resume_asset")
                or local_lead.get("asset")
                or _text(local_lead.get("platform")).lower() == "manual"
            )
        )
        if has_local_tailored:
            context = _manual_fill_context(repo, local_lead)
            if output_dir:
                try:
                    out_path = Path(output_dir)
                    out_path.mkdir(parents=True, exist_ok=True)
                    src_pdf = Path(context.get("resume_path") or "")
                    if src_pdf.is_file():
                        dst_pdf = out_path / src_pdf.name
                        if dst_pdf.resolve() != src_pdf.resolve():
                            import shutil
                            shutil.copy2(src_pdf, dst_pdf)
                            context["resume_path"] = str(dst_pdf)
                            context["resume_paths"] = {**context.get("resume_paths", {}), "pdf": str(dst_pdf)}
                except Exception as exc:
                    _log.warning("could not copy resume to output_dir: %s", exc)
            if hasattr(repo.leads, "save_prepared_fill_context"):
                repo.leads.save_prepared_fill_context(job_id, context)
        else:
            try:
                context = await create_corpus_client().prepare_application(
                    job_id, check_live=(req or ApplyRequest()).check_live, output_dir=output_dir
                )
            except CorpusUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

        if context.get("expired"):
            # Worth surfacing rather than silently proceeding: applying to a closed posting wastes
            # the user's time and pollutes the pipeline.
            raise HTTPException(status_code=409, detail="that posting is no longer open")

        paths = context.get("resume_paths") or {}
        _record(job_id, "apply_opened")
        await _broadcast(
            "apply_ready",
            f"Résumé written to {paths.get('pdf', 'the configured folder')} — opening the application",
        )
        return {
            "ok": True,
            "job_id": job_id,
            "apply_url": context.get("apply_url"),
            "resume_paths": paths,
            "fill_context": context,
        }

    @router.get("/fill-context")
    async def fill_context(url: str | None = None) -> dict:
        """The fill context for a portal URL, read by the extension for its active tab.

        Matching is by URL host on the corpus side, which beats recency: with several prepared
        applications open in different tabs, "most recent" would fill the wrong form.
        """
        repo = get_repository()
        local_ctx = _local_fill_context(repo, url)
        corpus_ctx = None
        corpus_error: CorpusUnavailable | None = None
        try:
            corpus_ctx = await create_corpus_client().fill_context(url)
        except CorpusUnavailable as exc:
            corpus_error = exc

        if local_ctx and corpus_ctx:
            ctx = max((local_ctx, corpus_ctx), key=lambda item: _text(item.get("prepared_at")))
        else:
            ctx = local_ctx or corpus_ctx
        if ctx is None and corpus_error is not None:
            raise HTTPException(status_code=503, detail=str(corpus_error)) from corpus_error
        if ctx is None:
            raise HTTPException(status_code=404, detail="no prepared application matches that URL")
        ctx = dict(ctx)
        if ctx.get("resume_ref") and not ctx.get("resume_url"):
            ctx["resume_url"] = f"/api/v1/corpus-assets/{ctx['resume_ref']}"
        return ctx

    @router.get("/corpus-assets/{ref}")
    async def corpus_asset(ref: str) -> Response:
        """Authenticated gateway proxy used by the extension for corpus résumés."""
        try:
            content, content_type = await create_corpus_client().asset(ref)
        except CorpusUnavailable as exc:
            status = 404 if "404" in str(exc) else 503
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return Response(content=content, media_type=content_type)

    @router.post("/fill/decide")
    async def fill_decide(req: FillDecideRequest) -> dict:
        """Decide what each scanned form field should be filled with.

        The extension only reports what the page contains (DOM scanning); every value
        decision — profile facts, EEO defaults, option matching — is made here against
        the configured LLM, so the extension holds no model keys and runs no prompts.
        """
        if not req.fields:
            return {"fills": []}
        from llm import call_llm

        profile = await asyncio.to_thread(get_repository().profile.get_profile) or {}
        misc_repo = getattr(get_repository(), "misc", None)
        misc_text = ""
        if misc_repo is not None:
            try:
                misc_text = await asyncio.to_thread(
                    lambda: (misc_repo.get_misc() or {}).get("text", "")
                )
            except Exception:
                misc_text = ""
        prose = _fill_profile_prose(profile, misc_text)
        description = "\n".join(
            f'[{item.id}] {item.type if item.kind == "text" else item.kind} "{item.label[:300]}"'
            + (" (required)" if item.required else "")
            + (f' options: {" | ".join(o[:120] for o in item.options[:30])}' if item.options else "")
            for item in req.fields
        )
        try:
            decision = await asyncio.to_thread(
                call_llm, _FILL_SYSTEM, f"{prose}\n\nFORM FIELDS:\n{description}", FillDecision
            )
        except Exception as exc:  # noqa: BLE001 — a failed decision degrades to "nothing filled", never a 500 mid-apply
            _log.warning("fill decide failed for job=%s: %s", req.job_id, exc)
            return {"fills": []}
        known = {item.id for item in req.fields}
        fills = [
            {"id": line.id, "value": line.value[:2000]}
            for line in decision.fills
            if line.id in known and line.value
        ]
        return {"fills": fills}

    @router.post("/leads/{job_id}/filled")
    async def filled(job_id: str, req: FilledRequest) -> dict:
        """The extension reports what it filled, for the activity log.

        Recorded because §6 wants the whole activity in the pipeline, and because "the extension
        filled 9 of 14 fields" is the only way the user learns which portals it handles badly.
        """
        _record(job_id, f"extension_filled:{len(req.filled)}/{len(req.filled) + len(req.skipped)}")
        await _broadcast("fill_done", f"Extension filled {len(req.filled)} fields")
        return {"ok": True, "filled": len(req.filled), "skipped": len(req.skipped)}

    @router.post("/leads/{job_id}/applied")
    async def applied(job_id: str) -> dict:
        """The human confirms they submitted.

        The only place a lead becomes "applied". Nothing automatic reaches here — not the apply
        preparation, and not the extension, which cannot submit.
        """
        repo = get_repository()
        try:
            await asyncio.to_thread(repo.leads.update_lead_status, job_id, "applied")
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="lead not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await _broadcast("applied", f"Marked applied: {job_id}")
        return {"ok": True, "job_id": job_id, "status": "applied"}

    return router
