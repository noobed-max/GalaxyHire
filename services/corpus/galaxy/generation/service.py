"""Tailor orchestration (docs/05, docs/07 §3).

liveness → select → assemble → ATS score → lint, with an idempotency cache keyed by
(user, job, profile_version) so re-opening the apply panel never re-burns work (docs/05 §9).
Renders produce signed, TTL'd asset refs. Persists an application row per (user, job).
"""

from __future__ import annotations

import re
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import structlog
from sqlalchemy import text

from galaxy.common.config import get_settings
from galaxy.db.engine import get_sessionmaker
from galaxy.generation import assemble as assembler
from galaxy.generation import cover as cover_mod
from galaxy.generation import render as renderer
from galaxy.generation.assets import save_asset
from galaxy.generation.ats_score import score as ats_score
from galaxy.generation.models import ResumeDoc, SelectionResult
from galaxy.generation.reframe import Reframer, reframe_projects
from galaxy.generation.reframe import lint as lint_doc
from galaxy.generation.select import apply_overrides, select
from galaxy.ingestion.liveness import is_open
from galaxy.llm.client import LLMClient
from galaxy.models.profile import Profile
from galaxy.search import service as search_service
from galaxy.search.profiles import ProfileStore

log = structlog.get_logger(__name__)

_REFRAME_SYSTEM = (
    "You reword a single resume bullet to naturally emphasize the given job keywords. "
    "Absolute rule: introduce NO new facts — no numbers, dates, metrics, employers, tools, or "
    "technologies that are not already in the original bullet. If you cannot improve it without "
    "adding facts, return it unchanged. Output only the reworded bullet, one line, no quotes."
)


def _default_reframer() -> Reframer | None:
    """An LLM reframer, or None when no endpoint is configured (reframing then stays off).

    The fact-diff guard (reframe.safe_reframe) still vets every rewrite, so a hallucinating model
    can only ever return the original bullet — never inject an unearned claim (docs/05 §3).
    """
    settings = get_settings()
    if not settings.llm_base_url:
        return None
    client = LLMClient()

    async def reframe(original: str, keywords: list[str]) -> str:
        kw = ", ".join(keywords[:12])
        resp = await client.chat(
            [
                {"role": "system", "content": _REFRAME_SYSTEM},
                {"role": "user", "content": f"Keywords: {kw}\nBullet: {original}"},
            ],
            max_tokens=160,
            temperature=0.2,
        )
        content = resp.get("choices", [{}])[0].get("message", {}).get("content")
        return (content or original).strip()

    return reframe


async def _maybe_reframe(
    selection: SelectionResult, profile: Profile, job: dict, reframer: Reframer | None
) -> SelectionResult:
    """Return a reframed copy of `selection` (cache untouched), or the original when off."""
    if reframer is None:
        return selection
    verbatim_ids = {p.id for p in profile.projects if p.verbatim}
    new_projects = await reframe_projects(
        selection.projects, verbatim_ids, job.get("jd_keywords") or [], reframer
    )
    return replace(selection, projects=new_projects)


class JobExpired(Exception):
    """The posting is no longer open — tailoring is refused (HTTP 409)."""


class JobNotFound(Exception):
    pass


class ProfileMissing(Exception):
    pass


# in-process idempotency cache: (user, job, profile_version) -> SelectionResult (docs/05 §9)
_SELECTION_CACHE: dict[tuple[str, str, int], SelectionResult] = {}


async def _load(user_id: UUID, job_id: str) -> tuple[Profile, dict]:
    profile = await ProfileStore().get(user_id)
    if profile is None:
        raise ProfileMissing
    job = await search_service.get_job(job_id)
    if job is None:
        raise JobNotFound
    return profile, job


def _selection_for(profile: Profile, job: dict) -> SelectionResult:
    key = (str(profile.user_id), job["canonical_job_id"], profile.profile_version)
    cached = _SELECTION_CACHE.get(key)
    if cached is not None:
        return cached
    sel = select(job, profile)
    if not sel.projects and profile.projects:
        # Never empty-handed — but stay inside the role. `role_sliced_projects` is what
        # slice_for_role already matched by role_tags; falling back to `profile.projects` would
        # re-add work the slice deliberately excluded (§5).
        from galaxy.generation.assemble import empty_projects_fallback

        sel.projects = empty_projects_fallback(sel.role_sliced_projects or profile.projects)
    _SELECTION_CACHE[key] = sel
    return sel


async def tailor(
    user_id: UUID, job_id: str, *, check_live: bool = True, reframe: bool = False
) -> dict:
    profile, job = await _load(user_id, job_id)
    if check_live and not await is_open(job["primary_url"]):
        raise JobExpired
    selection = _selection_for(profile, job)
    # optional, opt-in, LLM-gated: reword non-verbatim bullets toward the job's vocabulary, with
    # every rewrite vetted by the fact-diff guard. Verbatim by default (docs/05 §3).
    doc_selection = await _maybe_reframe(
        selection, profile, job, _default_reframer() if reframe else None
    )
    doc = assembler.assemble(doc_selection, profile, job)
    ats = ats_score(doc, job)
    lint = lint_doc(doc)
    await _upsert_application(user_id, job_id, selection, ats.overall, job.get("primary_url"))
    return {
        "job": {"id": job_id, "title": job["title"], "company": job["company"], "url": job["primary_url"]},
        "selection": asdict(selection),
        "ats_score": asdict(ats),
        "lint": {
            "ats_safety": [asdict(i) for i in lint.ats_safety],
            "ai_risk": [asdict(i) for i in lint.ai_risk],
            "ok": lint.ok,
        },
    }


def _slug(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "job"


def resolve_output_dir(requested: str | None) -> Path:
    """Where to write generated résumés.

    §6 lets the user choose the folder, so a request may override the configured default. Only
    absolute paths are accepted: a relative one would resolve against the server's working
    directory, which is not what a user picking "my Documents folder" means, and would scatter files
    somewhere they'd never find them.
    """
    if requested:
        candidate = Path(requested).expanduser()
        if candidate.is_absolute():
            return candidate
        log.warning("ignoring relative resume output dir %r; using the configured default", requested)
    return Path(get_settings().resume_output_dir)


def _write_resume(job: dict, pdf: bytes, tex: str | None = None, output_dir: str | None = None) -> dict:
    """Write the rendered résumé to disk; return the absolute paths written.

    Writes the `.tex` next to the PDF when available. §5 wants the user to be able to edit and
    recompile what they applied with, and a PDF alone can't be edited — so the source ships with it
    rather than being something they have to come back to the app for.
    """
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    stem = f"{_slug(job.get('company'))}-{_slug(job.get('title'))}-{stamp}"

    pdf_path = out_dir / f"{stem}.pdf"
    pdf_path.write_bytes(pdf)
    written = {"pdf": str(pdf_path.resolve())}

    if tex:
        tex_path = out_dir / f"{stem}.tex"
        tex_path.write_text(tex, encoding="utf-8")
        written["tex"] = str(tex_path.resolve())
    return written


def _fill_context(
    profile: Profile, job: dict, selection: SelectionResult, ats_overall: float, ref: str, resume_path: str
) -> dict:
    """The per-job payload the fill-only extension reads: identity/anything-else are FIXED across
    applications; skills/projects/experience are the job-tailored, field-sliced set (docs/11)."""
    ident = profile.identity
    return {
        "job_id": job["canonical_job_id"],
        "company": job.get("company", ""),
        "title": job.get("title", ""),
        "apply_url": job.get("primary_url", ""),
        "identity": {
            "name": ident.name, "email": ident.email, "phone": ident.phone,
            "links": list(ident.links), "locations": list(ident.locations),
            "citizenship": ident.citizenship, "work_authorization": ident.work_authorization,
            "willing_to_relocate": ident.willing_to_relocate,
        },
        "anything_else": profile.anything_else,
        "skills": list(selection.skills),
        "projects": [
            {"title": p.title, "bullets": list(p.bullets), "skills": list(p.matched_skills)}
            for p in selection.projects
        ],
        "experience": [
            {
                "title": e.get("title", ""), "company": e.get("company", ""),
                "dates": e.get("dates", ""), "bullets": list(e.get("bullets") or []),
            }
            for e in (selection.experience or profile.experience)
            if isinstance(e, dict)
        ],
        "resume_ref": ref,
        "resume_path": resume_path,
        "ats_score": ats_overall,
        "prepared_at": datetime.now(UTC).isoformat(),
    }


async def prepare(
    user_id: UUID,
    job_id: str,
    *,
    check_live: bool = True,
    reframe: bool = False,
    output_dir: str | None = None,
) -> dict:
    """Apply-prepare (docs/11 §W1.4): tailor → render the resume to disk → build & persist the
    per-job fill context. Returns the fill context; the web UI opens `apply_url` from it."""
    profile, job = await _load(user_id, job_id)
    if check_live and not await is_open(job["primary_url"]):
        raise JobExpired
    selection = _selection_for(profile, job)
    doc_selection = await _maybe_reframe(
        selection, profile, job, _default_reframer() if reframe else None
    )
    doc = assembler.assemble(doc_selection, profile, job)
    ats = ats_score(doc, job)

    pdf_bytes = renderer.to_pdf(doc)
    ref = save_asset(pdf_bytes, "pdf")
    from galaxy.generation.latex import to_latex

    written = _write_resume(job, pdf_bytes, to_latex(doc), output_dir)
    resume_path = written["pdf"]

    context = _fill_context(profile, job, doc_selection, ats.overall, ref, resume_path)
    # Paths the UI shows the user and the extension attaches from.
    context["resume_paths"] = written
    await _upsert_application(user_id, job_id, doc_selection, ats.overall, job.get("primary_url"))
    await _save_fill_context(user_id, job_id, context, resume_path, ref)
    return context


async def override(user_id: UUID, job_id: str, pin: list[str], drop: list[str]) -> dict:
    profile, job = await _load(user_id, job_id)
    base = _selection_for(profile, job)
    updated = apply_overrides(base, profile, pin, drop)
    _SELECTION_CACHE[(str(user_id), job_id, profile.profile_version)] = updated
    doc = assembler.assemble(updated, profile, job)
    ats = ats_score(doc, job)
    await _upsert_application(user_id, job_id, updated, ats.overall, job.get("primary_url"))
    return {"selection": asdict(updated), "ats_score": asdict(ats)}


def _build_doc(profile: Profile, job: dict, selection: SelectionResult) -> ResumeDoc:
    return assembler.assemble(selection, profile, job)


async def render_asset(user_id: UUID, job_id: str, fmt: str, kind: str = "resume") -> dict:
    """Render resume or cover in `fmt`; return a signed asset ref (docs/07 §3)."""
    profile, job = await _load(user_id, job_id)
    selection = _selection_for(profile, job)

    if kind == "cover":
        letter = cover_mod.cover_letter(profile, job, selection)
        fn, _content_type, ext = renderer.RENDERERS.get(fmt, renderer.RENDERERS["md"])
        # cover is prose: wrap into a minimal ResumeDoc-free render
        data = _render_text(letter, fmt)
        ref = save_asset(data, ext if fmt in ("md", "txt", "html") else "txt")
        return {"asset_ref": ref, "format": fmt, "kind": "cover"}

    doc = _build_doc(profile, job, selection)
    if fmt not in renderer.RENDERERS:
        fmt = "pdf"
    fn, _content_type, ext = renderer.RENDERERS[fmt]
    data = fn(doc)
    if isinstance(data, str):
        data = data.encode("utf-8")
    ref = save_asset(data, ext)
    await _set_resume_ref(user_id, job_id, ref)
    return {"asset_ref": ref, "format": fmt, "kind": "resume"}


def _render_text(text_body: str, fmt: str) -> bytes:
    if fmt == "html":
        import html as _html

        return (
            "<!doctype html><meta charset='utf-8'><body style='font-family:Arial;max-width:640px;"
            f"margin:2em auto;white-space:pre-wrap'>{_html.escape(text_body)}</body>"
        ).encode()
    return text_body.encode("utf-8")


# --- application persistence ------------------------------------------------

# applied_url snapshots the URL used at tailor time so the pipeline row survives a later re-merge
# re-electing a different primary_url (docs/03 §7). Frozen once the row is marked Applied.
_UPSERT_APP = text(
    """
    INSERT INTO applications
        (id, user_id, canonical_job_id, status, ats_score, selected_project_ids, applied_url)
    VALUES (gen_random_uuid(), :u, :j, 'Tailored', :ats, CAST(:pids AS jsonb), :url)
    ON CONFLICT (user_id, canonical_job_id) DO UPDATE SET
        status = CASE WHEN applications.status = 'Applied' THEN applications.status ELSE 'Tailored' END,
        ats_score = EXCLUDED.ats_score,
        selected_project_ids = EXCLUDED.selected_project_ids,
        applied_url = CASE WHEN applications.status = 'Applied'
                           THEN applications.applied_url ELSE EXCLUDED.applied_url END,
        updated_at = now()
    """
)


async def _upsert_application(
    user_id: UUID, job_id: str, selection: SelectionResult, ats: float, applied_url: str | None
):
    import json

    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            _UPSERT_APP,
            {
                "u": str(user_id),
                "j": job_id,
                "ats": ats,
                "pids": json.dumps([p.project_id for p in selection.projects]),
                "url": applied_url,
            },
        )
        await s.commit()


async def _save_fill_context(user_id: UUID, job_id: str, context: dict, resume_path: str, ref: str):
    import json

    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE applications SET fill_context = CAST(:fc AS jsonb), resume_path = :rp, "
                "resume_pdf_ref = :ref, updated_at = now() "
                "WHERE user_id = :u AND canonical_job_id = :j"
            ),
            {"fc": json.dumps(context), "rp": resume_path, "ref": ref,
             "u": str(user_id), "j": job_id},
        )
        await s.commit()


async def _set_resume_ref(user_id: UUID, job_id: str, ref: str):
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE applications SET resume_pdf_ref = :r, updated_at = now() "
                "WHERE user_id = :u AND canonical_job_id = :j"
            ),
            {"r": ref, "u": str(user_id), "j": job_id},
        )
        await s.commit()
