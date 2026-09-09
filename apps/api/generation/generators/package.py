from __future__ import annotations

import hashlib
import os
import re

from data.repository import Repository, create_repository
from data.conflicts_detect import canonical_parent_key as _conflict_parent_key
from core.logging import get_logger
from core.generation_readiness import lead_generation_blocker

from generation.generators.base import _DocPackage  # noqa: F401
from generation.generators.cover_letter import (
    _normalize_package,
)
from generation.generators.drafting import _draft_package
from generation.generators.keywords import (
    _job_keyword_terms,
    _keyword_coverage,
    _verification_from_package,
)
from generation.generators.resume import (
    _build_proof,
    _fallback_package,
)
from generation.selection import apply_selection, build_selection_block, is_empty_selection
import generation.pdf_renderer as _pdf

_log = get_logger(__name__)
_assets = _pdf._assets


def _is_transient_llm_error(exc: Exception) -> bool:
    try:
        from llm.client import is_transient_llm_error
    except ImportError:
        return False
    return is_transient_llm_error(exc)


def _safe_job_id(value: object) -> str:
    raw = str(value or "manual").strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)
    safe = safe.strip("._-") or "manual"
    # If sanitizing was lossy, two distinct ids (e.g. "a/b" and "a:b") collapse to
    # the same stem and would overwrite each other's PDFs. Append a short digest
    # of the raw id to keep them distinct. Clean ids are left untouched so the
    # leads router can still reconstruct versioned filenames from the raw id.
    if safe != raw:
        digest = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:8]
        safe = f"{safe}_{digest}"
    return safe


def get_profile(repo: Repository | None = None) -> dict:
    active_repo = repo or create_repository()
    profile = active_repo.profile.get_profile()
    # ensure_points derives selectable [{id,text}] bullets for rows that lack
    # them (legacy blobs). Lazy import_module: generation->profile is a forbidden
    # static import (import-boundary test) but the runtime call is sanctioned.
    from importlib import import_module

    try:
        return import_module("profile.ingest_parse").ensure_points(profile)
    except Exception:
        return profile


def _clean(text: str) -> str:
    return _pdf.clean(text)


def _strip_inline(text: str) -> str:
    return _pdf.strip_inline(text)


def _render_resume_template(md_text: str, filename: str) -> str:
    _pdf._assets = _assets
    os.makedirs(_assets, exist_ok=True)
    return _pdf.render_resume_template(md_text, filename)


def _render(md_text: str, filename: str, kind: str = "resume", verbatim: bool = False,
            stats: dict | None = None) -> str:
    _pdf._assets = _assets
    os.makedirs(_assets, exist_ok=True)
    return _pdf.render(md_text, filename, kind=kind, verbatim=verbatim, stats_out=stats)


def _scope_profile_to_tag(profile: dict, tag_id: str, repo: Repository) -> dict:
    """Bullet-level tag scoping (My-Resume-Maker tracks, adapted).

    The profile is the master superset; a selected tag narrows which points
    reach drafting. Untagged points are universal material and always stay;
    points carrying the chosen tag stay; points tagged only with OTHER tags
    are scoped out. Text is never altered — selection only decides inclusion,
    verbatim."""
    if not tag_id:
        return profile
    excluded = repo.point_tags.scoped_point_ids(tag_id)
    if not excluded:
        return profile

    # Keep the master profile immutable.  In particular, rebuilding ``d`` /
    # ``impact`` below must not rewrite the snapshot that other tags use.
    from copy import deepcopy

    from importlib import import_module

    point_module = import_module("profile.ingest_parse")
    points_with_ids = point_module.points_with_ids
    point_id = point_module.point_id

    def _parent_id_for_row(row: dict, point_kind: str) -> str:
        value = str(row.get("id") or "")
        if value:
            return value
        if point_kind == "experience":
            raw = f"{row.get('role') or ''}{row.get('co') or row.get('company') or ''}"
        else:
            raw = str(row.get("title") or "")
        return hashlib.md5(raw.encode()).hexdigest()[:12] if raw else ""

    scoped = deepcopy(profile)

    def keep_skills(items: list) -> list:
        out = excluded.get("skill", set())
        if not out:
            return items
        kept = []
        for item in items:
            if not isinstance(item, dict):
                kept.append(item)
                continue
            item_id = str(item.get("id") or "")
            if not item_id:
                name = str(item.get("n") or item.get("name") or "")
                item_id = hashlib.md5(name.encode()).hexdigest()[:12] if name else ""
            if item_id not in out:
                kept.append(item)
        dropped = len(items) - len(kept)
        if dropped:
            _log.info("tag %s scoped out %d skill point(s)", tag_id[:12], dropped)
        return kept

    def keep_entities(items: list, point_kind: str, text_key: str) -> list:
        excluded_ids = {str(value) for value in excluded.get(point_kind, set())}
        if not excluded_ids:
            return items
        kept_rows: list[dict] = []
        dropped_points = 0
        dropped_entities = 0
        for row in items:
            if not isinstance(row, dict):
                continue
            row_parent_id = _parent_id_for_row(row, point_kind)
            raw_points = row.get("points")
            if isinstance(raw_points, list) and raw_points:
                points = [
                    {
                        **point,
                        "id": str(point.get("id") or point_id(row_parent_id, str(point.get("text"))))
                    }
                    for point in raw_points
                    if isinstance(point, dict) and point.get("text")
                ]
            else:
                blob = str(row.get(text_key) or "")
                points = points_with_ids(row_parent_id, blob) if blob else []

            visible_points = [
                point for point in points
                if str(point.get("id", "")) not in excluded_ids
            ]
            dropped_points += len(points) - len(visible_points)

            # An entity tagged only to another track is still retained when at
            # least one child point is universal/selected.  If no child is
            # visible, the entity itself must be in-scope to survive.
            if row_parent_id in excluded_ids and points and not visible_points:
                dropped_entities += 1
                continue
            if row_parent_id in excluded_ids and not points:
                dropped_entities += 1
                continue

            copy = dict(row)
            if points:
                copy["points"] = visible_points
                copy[text_key] = "\n".join(str(point["text"]) for point in visible_points)
            elif isinstance(raw_points, list):
                copy["points"] = []
                if text_key in copy:
                    copy[text_key] = ""
            kept_rows.append(copy)

        if dropped_points:
            _log.info("tag %s scoped out %d %s bullet point(s)", tag_id[:12], dropped_points, point_kind)
        if dropped_entities:
            _log.info("tag %s scoped out %d %s entr(y/ies)", tag_id[:12], dropped_entities, point_kind)
        return kept_rows

    scoped["skills"] = keep_skills(scoped.get("skills", []) or [])
    scoped["projects"] = keep_entities(scoped.get("projects", []) or [], "project", "impact")
    scoped["exp"] = keep_entities(scoped.get("exp", []) or [], "experience", "d")
    return scoped


def _drop_conflict_duplicates(profile: dict, repo: Repository) -> dict:
    """One wording per conflict group, per build (MRM note-pill contract).

    A resolved group ("picked") keeps exactly the picked wording; an open group
    keeps the member earliest in the profile's own order (deterministic). Both
    wordings survive for keep_both / dismissed groups, and a group never
    constrains a build unless >= 2 of its members survived tag scoping. Nothing
    is deleted from storage — this only narrows the already-scoped copy."""
    try:
        groups = repo.conflicts.list_groups()
    except Exception as exc:
        _log.warning("conflict dedupe skipped (%s)", exc)
        return profile
    if not groups:
        return profile

    skills = [item for item in (profile.get("skills") or []) if isinstance(item, dict)]
    projects = [item for item in (profile.get("projects") or []) if isinstance(item, dict)]
    exp = [item for item in (profile.get("exp") or []) if isinstance(item, dict)]

    # Presence + deterministic order over bullet children only.  Experience /
    # project rows are entity identity and skills are standalone identity data;
    # neither can be a duplicate candidate or be removed by a conflict group.
    order: dict[tuple[str, str], int] = {}
    parent_by_point: dict[tuple[str, str], str] = {}

    def register(kind: str, unit_id: str, parent_id: str, parent_key: str) -> None:
        pair = (kind, str(unit_id))
        order.setdefault(pair, len(order))
        parent_by_point.setdefault(pair, str(parent_key or parent_id))

    for row in projects:
        parent_id = str(row.get("id", ""))
        header = str(row.get("title") or "").strip()
        parent_key = _conflict_parent_key("project", header)
        for point in row.get("points") or []:
            if isinstance(point, dict) and point.get("id"):
                register("project", point["id"], parent_id, parent_key)
    for row in exp:
        parent_id = str(row.get("id", ""))
        role = str(row.get("role") or "").strip()
        company = str(row.get("co") or row.get("company") or "").strip()
        parent_key = _conflict_parent_key("experience", f"role {role} company {company}")
        for point in row.get("points") or []:
            if isinstance(point, dict) and point.get("id"):
                register("experience", point["id"], parent_id, parent_key)
    if not order:
        return profile

    drop: set[tuple[str, str]] = set()
    for group in groups:
        if group.get("status") in ("keep_both", "dismissed"):
            continue
        members = [
            (str(m.get("point_kind")), str(m.get("point_id")))
            for m in (group.get("members") or [])
            if isinstance(m, dict)
        ]
        present = [(kind, pid) for kind, pid in members if (kind, pid) in order]
        if len(present) < 2:
            continue  # scoping already removed the twin; nothing to enforce
        # A legacy group can contain bullets from different entities due to the
        # old top-level detector.  Never let that group delete wording across
        # jobs/projects; stale memberships are repaired by the next sync/list.
        parent_scopes = {parent_by_point[pair] for pair in present}
        if len(parent_scopes) != 1:
            continue
        picked = (str(group.get("picked_kind") or ""), str(group.get("picked_id") or ""))
        keeper = picked if group.get("status") == "picked" and picked in order else min(present, key=lambda pair: order[pair])
        for pair in present:
            if pair != keeper:
                drop.add(pair)
        _log.info(
            "conflict group %s kept 1 of %d",
            group.get("id", "?"), len(present),
        )
    if not drop:
        return profile

    def filter_rows(rows: list[dict], kind: str) -> list[dict]:
        out = []
        for row in rows:
            if (kind, str(row.get("id", ""))) in drop:
                continue
            points = row.get("points")
            if isinstance(points, list) and points:
                kept_points = [
                    point for point in points
                    if not (isinstance(point, dict)
                            and (kind, str(point.get("id", ""))) in drop)
                ]
                if len(kept_points) != len(points):
                    row = {**row, "points": kept_points}
            out.append(row)
        return out

    scoped = dict(profile)
    scoped["skills"] = [row for row in skills if ("skill", str(row.get("id", ""))) not in drop]
    scoped["projects"] = filter_rows(projects, "project")
    scoped["exp"] = filter_rows(exp, "experience")
    return scoped


def run_package(lead: dict, template: str = "", repo: Repository | None = None, cover_letter_base: str = "", tag_id: str = "", selection: dict | None = None, contact_kinds: list[str] | None = None, show_location: bool | None = None) -> dict:
    blocked_reason = lead_generation_blocker(lead)
    if blocked_reason:
        raise ValueError(blocked_reason)
    # Fail loudly if a key-based provider is selected without a key, instead of
    # silently drafting with an empty LLM result and shipping a generic resume
    # marked "approved". Keyless providers (ollama / CLIs) pass through.
    from llm.client import assert_llm_configured

    assert_llm_configured("generator")
    repo = repo or create_repository()
    profile = get_profile(repo)
    if tag_id:
        profile = _scope_profile_to_tag(profile, tag_id, repo)
    # Unconditional: near-duplicate wordings must never both reach a build,
    # tag selected or not (a group only constrains when >=2 members survived).
    profile = _drop_conflict_duplicates(profile, repo)
    verbatim = False
    if not is_empty_selection(selection):
        profile, dropped = apply_selection(profile, selection, repo)
        verbatim = True
        if dropped:
            _log.info("doc selection dropped %d stale id(s)", dropped)
    selection_block = build_selection_block(profile) if verbatim else ""
    proof = _build_proof(profile)
    keyword_text = "\n".join([
        str(lead.get("title", "")),
        str(lead.get("description", "")),
        str(lead.get("reason", "")),
        "\n".join(str(value) for value in lead.get("match_points", []) or []),
        "\n".join(str(value) for value in lead.get("gaps", []) or []),
    ])
    keyword_candidates = _job_keyword_terms(keyword_text)
    lead_with_ctx = {
        **lead,
        "candidate_name": profile.get("n", ""),
        "keyword_candidates": keyword_candidates,
    }

    try:
        package = _draft_package(profile, proof, lead_with_ctx, template=template, cover_letter_base=cover_letter_base, selection_block=selection_block, contact_kinds=contact_kinds, show_location=show_location)
        package = _normalize_package(package, profile, lead_with_ctx, template=template)
    except Exception as exc:
        if _is_transient_llm_error(exc):
            # Rate limits / timeouts / 5xx: surface to the router's retry path
            # (503 + Retry-After, lead stays in "tailoring") instead of silently
            # shipping an untailored fallback resume marked "approved".
            raise
        _log.warning(
            "LLM draft failed for %s; using local fallback package: %s",
            lead.get("job_id", "?"),
            exc,
        )
        package = _fallback_package(profile, lead_with_ctx, template=template, selection=selection,
                                     contact_kinds=contact_kinds)
    verified_terms, verification = _verification_from_package(keyword_candidates, package)
    keyword_coverage = _keyword_coverage(
        profile,
        lead_with_ctx,
        package.resume_markdown,
        jd_terms=verified_terms,
        verification=verification,
    )

    job_id = _safe_job_id(lead.get("job_id") or lead.get("id"))
    render_stats: dict = {}
    try:
        current_version = repo.leads.get_resume_version(job_id)
        new_version = current_version + 1
        resume_path = _render(package.resume_markdown, f"{job_id}_v{new_version}.pdf", kind="resume",
                              verbatim=verbatim, stats=render_stats)
        cover_letter_path = _render(package.cover_letter_markdown, f"{job_id}_cl_v{new_version}.pdf", kind="cover")
        try:
            repo.leads.save_generated_asset_version(job_id, resume_path, cover_letter_path, new_version)
        except Exception as exc:
            _log.warning("asset version persistence skipped for %s: %s", job_id, exc)
    except Exception as exc:
        _log.warning("PDF render failed for %s; retrying with local fallback package: %s", job_id, exc)
        try:
            package = _fallback_package(profile, lead_with_ctx, template=template, selection=selection,
                                     contact_kinds=contact_kinds)
            verified_terms, verification = _verification_from_package(keyword_candidates, package)
            keyword_coverage = _keyword_coverage(
                profile,
                lead_with_ctx,
                package.resume_markdown,
                jd_terms=verified_terms,
                verification=verification,
            )
            current_version = repo.leads.get_resume_version(job_id)
            new_version = current_version + 1
            render_stats = {}
            resume_path = _render(package.resume_markdown, f"{job_id}_v{new_version}.pdf", kind="resume",
                                  verbatim=verbatim, stats=render_stats)
            cover_letter_path = _render(package.cover_letter_markdown, f"{job_id}_cl_v{new_version}.pdf", kind="cover")
            try:
                repo.leads.save_generated_asset_version(job_id, resume_path, cover_letter_path, new_version)
            except Exception as persist_exc:
                _log.warning("asset version persistence skipped for %s: %s", job_id, persist_exc)
        except Exception as fallback_exc:
            _log.error("Fallback PDF render failed for %s: %s", job_id, fallback_exc)
            raise RuntimeError(f"PDF render failed: {fallback_exc}") from fallback_exc

    result = {
        "resume": resume_path,
        "cover_letter": cover_letter_path,
        "selected_projects": package.selected_projects,
        "founder_message": (package.founder_message or "").strip(),
        "linkedin_note": (package.linkedin_note or "").strip(),
        "cold_email": (package.cold_email or "").strip(),
        "keyword_coverage": keyword_coverage,
    }
    if render_stats.get("used_ratio") is not None:
        result["render_ratio"] = round(float(render_stats["used_ratio"]), 4)
    return result


def run(lead: dict, template: str = "") -> str:
    return run_package(lead, template=template)["resume"]
