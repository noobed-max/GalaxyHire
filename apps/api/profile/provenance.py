"""Deterministic resume-to-profile provenance.

The profile graph is intentionally a master superset.  This module is the small
bridge between a tagged resume document and that superset: it records only the
canonical points evidenced by one incoming document and can repair old document
rows from their stored text.  It must remain deterministic and must not invoke
an LLM; a missing or ambiguous match is reported instead of broadening a tag.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from data.graph import profile as graph_profile
from profile.ingest_documents import _document
from profile.ingest_parse import (
    canonical_point_key,
    canonical_experience_key,
    canonical_project_key,
    point_id,
    points_with_ids,
    split_points,
)


def _as_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _row_id(row: dict, fallback: str) -> str:
    return _text(row.get("id")) or fallback


def _profile_rows(profile: dict | None, key: str) -> list[dict]:
    if not isinstance(profile, dict):
        return []
    if key == "exp":
        rows = profile.get("exp") or profile.get("experiences") or profile.get("experience") or []
    else:
        rows = profile.get(key) or []
    return [row for row in rows if isinstance(row, dict)]


def _entity_key(kind: str, row: dict) -> str:
    if kind == "experience":
        return canonical_experience_key(
            _text(row.get("role") or row.get("position")),
            _text(row.get("co") or row.get("company")),
        )
    return canonical_project_key(
        _text(row.get("title") or row.get("name")), _text(row.get("repo"))
    )


def _find_existing_entity(existing: dict | None, kind: str, incoming: dict) -> dict | None:
    rows = _profile_rows(existing, "exp" if kind == "experience" else "projects")
    matched_id = _text(incoming.get("matched_entity_id"))
    if matched_id:
        for row in rows:
            if _text(row.get("id")) == matched_id:
                return row
    key = _entity_key(kind, incoming)
    if not key:
        return None
    for row in rows:
        if _entity_key(kind, row) == key:
            return row
    return None


def _existing_points(row: dict | None, parent_id: str, blob: str = "") -> list[dict]:
    if not row:
        return []
    points = row.get("points")
    if isinstance(points, list) and points:
        return [point for point in points if isinstance(point, dict) and point.get("text")]
    source = _text(row.get("d") or row.get("description") or row.get("impact") or blob)
    return points_with_ids(parent_id, source) if source else []


def _point_texts(
    entry: dict,
    text_key: str,
    pending_keys: set[str],
) -> list[tuple[str, str]]:
    """Return incoming ``(point_id, text)`` candidates, excluding pending pairs.

    A parser may provide explicit ``points``/``new_points`` or only the legacy
    description blob.  In all cases the source is this incoming entry, never the
    complete existing parent row.
    """
    explicit_points = entry.get("points")
    if isinstance(explicit_points, list) and explicit_points:
        source: list[Any] = explicit_points
    elif entry.get("new_points"):
        raw_new_points = entry.get("new_points") or []
        source = list(raw_new_points) if isinstance(raw_new_points, list) else [raw_new_points]
    else:
        source = split_points(_text(entry.get(text_key)))

    candidates: list[tuple[str, str]] = []
    for item in source:
        if isinstance(item, dict):
            text = _text(item.get("text"))
            pid = _text(item.get("id"))
        else:
            text = _text(item)
            pid = ""
        key = canonical_point_key(text)
        if not text or not key or key in pending_keys:
            continue
        candidates.append((pid, text))
    return candidates


def _incoming_point_ids(
    entry: dict,
    *,
    parent_id: str,
    text_key: str,
    existing_row: dict | None,
) -> list[str]:
    exact_matches = [_as_dict(item) for item in entry.get("exact_matches") or []]
    similar_pairs = [_as_dict(item) for item in entry.get("similar_pairs") or []]
    exact_by_key = {
        canonical_point_key(_text(item.get("text"))): _text(item.get("existing_point_id"))
        for item in exact_matches
        if _text(item.get("text")) and _text(item.get("existing_point_id"))
    }
    pending_keys = {
        canonical_point_key(_text(item.get("new_text")))
        for item in similar_pairs
        if canonical_point_key(_text(item.get("new_text")))
    }
    existing_points = _existing_points(existing_row, parent_id, _text(entry.get(text_key)))
    existing_by_key = {
        canonical_point_key(_text(point.get("text"))): _text(point.get("id"))
        for point in existing_points
        if canonical_point_key(_text(point.get("text"))) and _text(point.get("id"))
    }

    out: list[str] = []
    seen: set[str] = set()

    # Explicit exact matches are authoritative even if an LLM omitted the
    # canonical text from the incoming description blob.
    for item in exact_matches:
        pid = _text(item.get("existing_point_id"))
        if pid and pid not in seen:
            seen.add(pid)
            out.append(pid)

    for explicit_pid, text in _point_texts(entry, text_key, pending_keys):
        key = canonical_point_key(text)
        pid = explicit_pid or exact_by_key.get(key) or existing_by_key.get(key) or point_id(parent_id, text)
        if pid and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def collect_resume_point_tags(
    resume_profile: Any,
    tag_id: str,
    existing_profile: dict | None = None,
) -> list[tuple[str, str, str]]:
    """Collect only canonical point IDs evidenced by one incoming extraction.

    Parent experience/project IDs are included because an entity can carry its
    own track membership.  Similar alternatives are deliberately excluded until
    ``keep_both``, ``keep_existing``, or ``use_new`` resolves them.
    """
    tag_id = _text(tag_id)
    if not tag_id:
        return []
    data = _as_dict(resume_profile)
    existing = existing_profile if isinstance(existing_profile, dict) else {}
    triples: list[tuple[str, str, str]] = []

    existing_skills = {
        canonical_point_key(_text(row.get("n") or row.get("name"))): _row_id(row, "")
        for row in _profile_rows(existing, "skills")
        if canonical_point_key(_text(row.get("n") or row.get("name")))
    }
    for raw_skill in data.get("skills", []) or []:
        skill = _as_dict(raw_skill)
        name = _text(skill.get("n") or skill.get("name")) or _text(raw_skill)
        if name:
            skill_id = _row_id(skill, "") or existing_skills.get(canonical_point_key(name)) or graph_profile.hash_id(name)
            triples.append(("skill", skill_id, tag_id))

    for raw_entry in data.get("exp", []) or data.get("experiences", []) or data.get("experience", []) or []:
        entry = _as_dict(raw_entry)
        role = _text(entry.get("role") or entry.get("position"))
        company = _text(entry.get("co") or entry.get("company"))
        if not (role or company):
            continue
        fallback_id = graph_profile.hash_id(role + company)
        existing_row = _find_existing_entity(existing, "experience", entry)
        parent_id = _row_id(existing_row or entry, fallback_id)
        triples.append(("experience", parent_id, tag_id))
        for pid in _incoming_point_ids(
            entry,
            parent_id=parent_id,
            text_key="d" if _text(entry.get("d")) else "description",
            existing_row=existing_row,
        ):
            triples.append(("experience", pid, tag_id))

    for raw_project in data.get("projects", []) or []:
        project = _as_dict(raw_project)
        title = _text(project.get("title") or project.get("name"))
        if not title:
            continue
        fallback_id = graph_profile.hash_id(title)
        existing_row = _find_existing_entity(existing, "project", project)
        parent_id = _row_id(existing_row or project, fallback_id)
        triples.append(("project", parent_id, tag_id))
        for pid in _incoming_point_ids(
            project,
            parent_id=parent_id,
            text_key="impact" if _text(project.get("impact")) else "description",
            existing_row=existing_row,
        ):
            triples.append(("project", pid, tag_id))

    # Preserve insertion order while avoiding duplicate triples from an exact
    # match that also appeared in the source description.
    return list(dict.fromkeys(triples))


def _contains_evidence(
    value: str,
    evidence_lower: str,
    evidence_key: str,
    *,
    min_key_len: int,
    exact_phrase: bool = False,
) -> bool:
    key = canonical_point_key(value)
    if not key:
        return False
    # Match a sequence of words rather than a compact key substring.  A plain
    # ``key in evidence_key`` would incorrectly match an old bullet inside a
    # longer pending similar alternative ("built backend" in "built backend
    # with Go").
    tokens = re.findall(r"[a-z0-9+#]+", _text(value).lower())
    if not tokens:
        return False
    escaped = r"[^a-z0-9+#]+".join(re.escape(token) for token in tokens)
    pattern = rf"(?<![a-z0-9+#]){escaped}(?![a-z0-9+#])"
    if exact_phrase:
        # Bullet evidence must end at sentence/line punctuation, a bullet
        # marker, or end-of-document—not merely before another ordinary word.
        pattern += r"(?=$|[\r\n]|[.!?;,:)\]}](?:\s|$)|\s*[-•])"
    return bool(re.search(pattern, evidence_lower))


def _matched_point_ids(row: dict, kind: str, evidence_lower: str, evidence_key: str) -> list[str]:
    parent_id = _text(row.get("id"))
    if not parent_id:
        if kind == "experience":
            parent_id = graph_profile.hash_id(
                _text(row.get("role") or row.get("position"))
                + _text(row.get("co") or row.get("company"))
            )
        else:
            parent_id = graph_profile.hash_id(_text(row.get("title") or row.get("name")))
    points = _existing_points(row, parent_id)
    matched: list[str] = []
    for point in points:
        text = _text(point.get("text"))
        pid = _text(point.get("id")) or point_id(parent_id, text)
        if text and _contains_evidence(text, evidence_lower, evidence_key, min_key_len=8, exact_phrase=True):
            matched.append(pid)
    return list(dict.fromkeys(matched))


def _entity_is_evidenced(row: dict, kind: str, evidence_lower: str, evidence_key: str) -> bool:
    if kind == "experience":
        markers = [_text(row.get("role") or row.get("position")), _text(row.get("co") or row.get("company"))]
    else:
        markers = [_text(row.get("title") or row.get("name"))]
    return any(_contains_evidence(marker, evidence_lower, evidence_key, min_key_len=4) for marker in markers if marker)


def match_document_profile_points(document: dict, profile: dict) -> list[tuple[str, str, str]]:
    """Conservatively match one stored resume excerpt to canonical profile IDs."""
    tag_id = _text(document.get("tag_id"))
    if not tag_id or not isinstance(profile, dict):
        return []
    evidence = _text(document.get("excerpt"))
    if not evidence and _text(document.get("file_path")):
        try:
            evidence = _text(_document(_text(document.get("file_path"))))
        except Exception:
            evidence = ""
    if not evidence:
        return []
    evidence_lower = evidence.lower()
    evidence_key = canonical_point_key(evidence)
    triples: list[tuple[str, str, str]] = []

    for skill in _profile_rows(profile, "skills"):
        name = _text(skill.get("n") or skill.get("name"))
        skill_id = _text(skill.get("id")) or graph_profile.hash_id(name)
        if name and _contains_evidence(name, evidence_lower, evidence_key, min_key_len=4):
            triples.append(("skill", skill_id, tag_id))

    for kind, profile_key in (("experience", "exp"), ("project", "projects")):
        for row in _profile_rows(profile, profile_key):
            if not _entity_is_evidenced(row, kind, evidence_lower, evidence_key):
                continue
            parent_id = _text(row.get("id"))
            if not parent_id:
                if kind == "experience":
                    parent_id = graph_profile.hash_id(
                        _text(row.get("role") or row.get("position"))
                        + _text(row.get("co") or row.get("company"))
                    )
                else:
                    parent_id = graph_profile.hash_id(_text(row.get("title") or row.get("name")))
            matched = _matched_point_ids(row, kind, evidence_lower, evidence_key)
            # If points exist, require at least one exact evidenced point before
            # assigning the parent tag. This prevents an entity marker alone from
            # making every legacy/unscoped bullet visible for that track.
            points = _existing_points(row, parent_id)
            if points and not matched:
                continue
            triples.append((kind, parent_id, tag_id))
            triples.extend((kind, pid, tag_id) for pid in matched)

    return list(dict.fromkeys(triples))


def reconcile_tagged_resume_document(
    document: dict | str,
    *,
    profile: dict | None = None,
    db_path: str | None = None,
) -> dict:
    """Repair one tagged resume document and return an auditable status report."""
    import data.sqlite.documents as documents
    import data.sqlite.point_tags as point_tags

    row = documents.get_document(document, db_path=db_path) if isinstance(document, str) else dict(document)
    if not row:
        return {"status": "not_found", "rows_inserted": 0, "rows_existing": 0, "candidates": 0}
    if row.get("kind") != "resume" or not _text(row.get("tag_id")):
        return {"status": "skipped", "document_id": row.get("id"), "rows_inserted": 0, "rows_existing": 0, "candidates": 0}
    if profile is None:
        profile = graph_profile.get_profile(db_path) if db_path else graph_profile.get_profile()
    triples = match_document_profile_points(row, profile or {})
    inserted = point_tags.add_point_tags_batch(triples, db_path=db_path)
    return {
        "status": "repaired" if inserted else ("already_complete" if triples else "no_match"),
        "document_id": row.get("id"),
        "tag_id": row.get("tag_id"),
        "candidates": len(triples),
        "rows_inserted": inserted,
        "rows_existing": max(0, len(triples) - inserted),
    }


def reconcile_tagged_resume_documents(
    *,
    document_id: str | None = None,
    tag_id: str | None = None,
    profile: dict | None = None,
    db_path: str | None = None,
) -> dict:
    """Repair all (or one) tagged resume rows without changing existing tags."""
    import data.sqlite.documents as documents

    if document_id:
        rows = [documents.get_document(document_id, db_path=db_path)]
    else:
        rows = documents.list_documents(kind="resume", tag_id=tag_id, db_path=db_path)
    rows = [row for row in rows if row]
    if not document_id:
        # Reconciliation is scoped to document tracks.  Untagged resumes are
        # universal source material and must not be reported as skipped work.
        rows = [row for row in rows if _text(row.get("tag_id"))]
    if document_id and not rows:
        return {
            "status": "not_found",
            "documents_scanned": 0,
            "documents_repaired": 0,
            "documents_with_matches": 0,
            "rows_inserted": 0,
            "rows_candidates": 0,
            "rows_existing": 0,
            "details": [],
        }
    if not rows:
        return {
            "status": "nothing_to_repair",
            "documents_scanned": 0,
            "documents_repaired": 0,
            "documents_with_matches": 0,
            "rows_inserted": 0,
            "rows_candidates": 0,
            "rows_existing": 0,
            "details": [],
        }
    if profile is None:
        profile = graph_profile.get_profile(db_path) if db_path else graph_profile.get_profile()
    details = [
        reconcile_tagged_resume_document(row, profile=profile, db_path=db_path)
        for row in rows
    ]
    inserted = sum(int(item.get("rows_inserted") or 0) for item in details)
    detail_statuses = {str(item.get("status") or "") for item in details}
    if detail_statuses and detail_statuses <= {"no_match", "skipped"}:
        status = "no_matches"
    elif "no_match" in detail_statuses or "skipped" in detail_statuses:
        status = "partial"
    else:
        status = "ok"
    return {
        "status": status if details else "nothing_to_repair",
        "documents_scanned": len(rows),
        "documents_repaired": sum(item.get("status") == "repaired" for item in details),
        "documents_with_matches": sum(bool(item.get("candidates")) for item in details),
        "rows_inserted": inserted,
        "rows_candidates": sum(int(item.get("candidates") or 0) for item in details),
        "rows_existing": sum(int(item.get("rows_existing") or 0) for item in details),
        "details": details,
    }


# Short aliases make the repair operation easy to discover from maintenance
# jobs and keep callers independent of the implementation's historical name.
backfill_tagged_resume_documents = reconcile_tagged_resume_documents
repair_tagged_resume_documents = reconcile_tagged_resume_documents
