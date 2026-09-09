"""Profile read + snapshot management for the graph layer.

Reads the profile from the persisted snapshot, the Kùzu graph, and the LanceDB
vectors, and merges those sources into one normalized profile. Owns the
snapshot load/save and the refresh that rebuilds the snapshot from graph +
vectors. Sits above base/deletions/vectors; does not import correlations or
mutations.
"""

from __future__ import annotations

import json
import logging
import re

from core.logging import get_logger
from data.conflicts_detect import canonical_parent_key
from data.graph.profile_base import (
    IDENTITY_KEYS,
    PROFILE_SNAPSHOT_KEY,
    _query_rows,
    empty_profile,
    normal_profile,
    profile_has_data,
    profile_has_structured_data,
    stack_list,
)
from data.graph.profile_deletions import apply_profile_deletions
from data.graph.profile_vectors import read_profile_from_vectors
from data.sqlite.settings import get_setting, save_settings

_log = get_logger(__name__)


def _merge_duplicate_entity_row(kind: str, target: dict, incoming: dict) -> None:
    """Merge text from duplicate graph/snapshot entity rows in place.

    The graph and snapshot can briefly contain the same entity under different
    legacy IDs.  Keep the first row/ID (tags and selections point there), while
    retaining distinct bullet lines and richer project metadata.
    """
    if kind == "exp":
        target_key = "d" if "d" in target else "description"
        incoming_text = str(incoming.get("d") or incoming.get("description") or "").strip()
        current = str(target.get(target_key) or "").strip()
        _merge_duplicate_points(target, incoming, text_key=target_key)
        current_keys = {re.sub(r"[^a-z0-9]+", "", line.lower()) for line in current.splitlines() if line.strip()}
        additions = [
            line.strip() for line in incoming_text.splitlines()
            if line.strip() and re.sub(r"[^a-z0-9]+", "", line.lower()) not in current_keys
        ]
        if additions:
            target[target_key] = "\n".join(part for part in (current, *additions) if part)
        if not target.get("period") and incoming.get("period"):
            target["period"] = incoming["period"]
        return

    if kind == "projects":
        current = str(target.get("impact") or "").strip()
        incoming_text = str(incoming.get("impact") or incoming.get("description") or "").strip()
        _merge_duplicate_points(target, incoming, text_key="impact")
        current_keys = {re.sub(r"[^a-z0-9]+", "", line.lower()) for line in current.splitlines() if line.strip()}
        additions = [
            line.strip() for line in incoming_text.splitlines()
            if line.strip() and re.sub(r"[^a-z0-9]+", "", line.lower()) not in current_keys
        ]
        if additions:
            target["impact"] = "\n".join(part for part in (current, *additions) if part)
        if not target.get("repo") and incoming.get("repo"):
            target["repo"] = incoming["repo"]
        current_stack = target.get("stack") or []
        incoming_stack = incoming.get("stack") or []
        if not isinstance(current_stack, list):
            current_stack = [part.strip() for part in str(current_stack).split(",") if part.strip()]
        if not isinstance(incoming_stack, list):
            incoming_stack = [part.strip() for part in str(incoming_stack).split(",") if part.strip()]
        seen_stack = {str(part).casefold() for part in current_stack}
        for part in incoming_stack:
            stack_key = str(part).casefold()
            if stack_key and stack_key not in seen_stack:
                seen_stack.add(stack_key)
                current_stack.append(part)
        target["stack"] = current_stack


def _merge_duplicate_points(target: dict, incoming: dict, *, text_key: str) -> None:
    incoming_points = incoming.get("points")
    if not isinstance(incoming_points, list) or not incoming_points:
        return
    target_points = target.get("points")
    if not isinstance(target_points, list):
        target_points = []
    seen_ids = {str(point.get("id")) for point in target_points if isinstance(point, dict) and point.get("id")}
    seen_text = {
        re.sub(r"[^a-z0-9]+", "", str(point.get("text") or "").lower())
        for point in target_points
        if isinstance(point, dict) and point.get("text")
    }
    seen_text.update(
        re.sub(r"[^a-z0-9]+", "", line.lower())
        for line in str(target.get(text_key) or "").splitlines()
        if line.strip()
    )
    additions: list[str] = []
    for point in incoming_points:
        if not isinstance(point, dict) or not point.get("text"):
            continue
        pid = str(point.get("id") or "")
        text = str(point["text"])
        text_key_value = re.sub(r"[^a-z0-9]+", "", text.lower())
        if (pid and pid in seen_ids) or (text_key_value and text_key_value in seen_text):
            continue
        target_points.append({"id": pid, "text": text} if pid else {"text": text})
        if pid:
            seen_ids.add(pid)
        if text_key_value:
            seen_text.add(text_key_value)
        additions.append(text)
    if target_points:
        target["points"] = target_points
        if additions:
            current_text = str(target.get(text_key) or "").strip()
            target[text_key] = "\n".join(part for part in (current_text, *additions) if part)


def load_profile_snapshot(db_path: str | None = None) -> dict:
    try:
        raw = get_setting(PROFILE_SNAPSHOT_KEY, "", db_path) if db_path else get_setting(PROFILE_SNAPSHOT_KEY)
        if not raw:
            return {}
        profile = apply_profile_deletions(json.loads(raw or "{}"), db_path)
        return profile if profile_has_data(profile) else {}
    except Exception as log_exc:
        logging.getLogger(__name__).warning('suppressed exception in load_profile_snapshot: %s', log_exc)
        return {}


def save_profile_snapshot(profile: dict, db_path: str | None = None, *, allow_empty: bool = False) -> None:
    profile = apply_profile_deletions(profile, db_path)
    if not allow_empty and not profile_has_data(profile):
        return
    try:
        # Rolling backup before overwrite: the snapshot is the single copy of the
        # user's profile (the graph may be empty/disabled), so a bad ingest used to
        # be unrecoverable. Keep the last 3 snapshots in sibling settings keys.
        try:
            current = get_setting(PROFILE_SNAPSHOT_KEY, "", db_path)
            if current:
                bak1 = get_setting(f"{PROFILE_SNAPSHOT_KEY}_bak1", "", db_path)
                bak2 = get_setting(f"{PROFILE_SNAPSHOT_KEY}_bak2", "", db_path)
                shifts = {}
                if bak2:
                    shifts[f"{PROFILE_SNAPSHOT_KEY}_bak3"] = bak2
                if bak1:
                    shifts[f"{PROFILE_SNAPSHOT_KEY}_bak2"] = bak1
                shifts[f"{PROFILE_SNAPSHOT_KEY}_bak1"] = current
                if shifts:
                    save_settings(shifts, db_path)
        except Exception:
            pass
        payload = {PROFILE_SNAPSHOT_KEY: json.dumps(profile, ensure_ascii=False)}
        if db_path:
            save_settings(payload, db_path)
        else:
            save_settings(payload)
    except Exception as log_exc:
        logging.getLogger(__name__).warning('suppressed exception in save_profile_snapshot: %s', log_exc)
        pass


def read_profile_from_graph(*, require_graph: bool = False) -> dict:
    candidates = _query_rows("MATCH (n:Candidate) RETURN n.id, n.n, n.s", require_result=require_graph)
    if candidates:
        candidates.sort(
            key=lambda row: (
                0 if str(row[1] or "").strip().lower() in {"", "unknown", "candidate"} else 1,
                len(str(row[1] or "")) + len(str(row[2] or "")),
            ),
            reverse=True,
        )
        candidate = candidates[0]
    else:
        candidate = ["", "", ""]

    skills = []
    for row in _query_rows("MATCH (n:Skill) RETURN n.id, n.n, n.cat", require_result=require_graph):
        skills.append({"id": row[0], "n": row[1], "cat": row[2]})

    projects = []
    for row in _query_rows("MATCH (n:Project) RETURN n.id, n.title, n.stack, n.repo, n.impact", require_result=require_graph):
        projects.append({"id": row[0], "title": row[1], "stack": stack_list(row[2]), "repo": row[3], "impact": row[4]})

    experience = []
    for row in _query_rows("MATCH (n:Experience) RETURN n.id, n.role, n.co, n.period, n.d", require_result=require_graph):
        experience.append({"id": row[0], "role": row[1], "co": row[2], "period": row[3], "d": row[4]})

    def read_text_nodes(label: str) -> list[str]:
        items: list[str] = []
        for row in _query_rows(f"MATCH (n:{label}) RETURN n.title", require_result=require_graph):
            text = str(row[0] or "").strip()
            if text:
                items.append(text)
        return items

    return apply_profile_deletions({
        "n": candidate[1],
        "s": candidate[2],
        "skills": skills,
        "projects": projects,
        "exp": experience,
        "certifications": read_text_nodes("Certification"),
        "education": read_text_nodes("Education"),
        "achievements": read_text_nodes("Achievement"),
        "identity": {key: get_setting(key, "") for key in IDENTITY_KEYS},
    })


def get_profile(db_path: str | None = None, *, prefer_snapshot: bool = True) -> dict:
    snapshot = load_profile_snapshot(db_path)
    if prefer_snapshot and profile_has_structured_data(snapshot):
        return snapshot
    merged = normal_profile(snapshot)
    hydrated = False
    source_has_data = False
    read_error: Exception | None = None
    try:
        graph_profile = normal_profile(read_profile_from_graph(require_graph=True))
        if profile_has_data(graph_profile):
            source_has_data = True
            merged = merge_profiles(merged, graph_profile)
            hydrated = hydrated or profile_has_structured_data(graph_profile)
    except Exception as exc:
        _log.warning("profile graph read skipped: %s", exc)
        read_error = exc

    vector_profile = read_profile_from_vectors(db_path)
    if profile_has_data(vector_profile):
        source_has_data = True
        merged = merge_profiles(merged, vector_profile)
        hydrated = hydrated or profile_has_structured_data(vector_profile)

    if snapshot and not source_has_data:
        return snapshot
    if profile_has_data(merged):
        if hydrated and profile_has_structured_data(merged):
            save_profile_snapshot(merged, db_path)
        return merged
    if snapshot:
        return snapshot
    if read_error:
        _log.error("profile read failed: %s", read_error)
    return empty_profile()


def merge_profiles(base: dict | None, incoming: dict | None) -> dict:
    merged = normal_profile(base)
    incoming = normal_profile(incoming)
    if str(incoming.get("n") or "").strip().lower() not in {"", "unknown", "candidate"}:
        merged["n"] = incoming.get("n", "")
    if str(incoming.get("s") or "").strip():
        merged["s"] = incoming.get("s", "")
    merged["identity"] = {**(merged.get("identity") or {}), **{k: v for k, v in (incoming.get("identity") or {}).items() if v}}
    for key, id_key in [("skills", "id"), ("projects", "id"), ("exp", "id")]:
        seen: set[str] = set()
        rows: list[dict] = []
        for item in [*(merged.get(key) or []), *(incoming.get(key) or [])]:
            if not isinstance(item, dict):
                continue
            if key == "exp" and (item.get("role") or item.get("co") or item.get("company")):
                entity_key = canonical_parent_key(
                    "experience",
                    "role " + str(item.get("role") or "").strip()
                    + " company " + str(item.get("co") or item.get("company") or "").strip(),
                )
                marker = "entity:" + entity_key if entity_key else ""
            elif key == "projects" and (item.get("title") or item.get("name")):
                entity_key = canonical_parent_key(
                    "project", str(item.get("title") or item.get("name") or "")
                )
                marker = "entity:" + entity_key if entity_key else ""
            else:
                marker = str(item.get(id_key) or item.get("n") or item.get("title") or item.get("role") or "").strip().lower()
            if not marker or marker in seen:
                if marker and marker in seen and rows:
                    target = next((row for row in rows if row.get("_merge_marker") == marker), None)
                    if target is not None:
                        _merge_duplicate_entity_row(key, target, item)
                continue
            seen.add(marker)
            # Keep the marker internal while collecting so a duplicate graph /
            # snapshot row can merge its richer text into the first row; remove
            # it before returning the profile.
            rows.append({**item, "_merge_marker": marker})
        for row in rows:
            row.pop("_merge_marker", None)
        merged[key] = rows
    for key in ["education", "certifications", "achievements"]:
        seen_text: set[str] = set()
        values: list[str] = []
        for item in [*(merged.get(key) or []), *(incoming.get(key) or [])]:
            text = str(item.get("title") if isinstance(item, dict) else item or "").strip()
            marker = text.lower()
            if text and marker not in seen_text:
                seen_text.add(marker)
                values.append(text)
        merged[key] = values
    return merged


def refresh_profile_snapshot(db_path: str | None = None) -> None:
    graph_profile = {}
    vector_profile = {}
    try:
        graph_profile = read_profile_from_graph()
    except Exception as exc:
        _log.warning("profile graph refresh skipped: %s", exc)
    try:
        vector_profile = read_profile_from_vectors(db_path)
    except Exception as exc:
        _log.warning("profile vector refresh skipped: %s", exc)
    profile = merge_profiles(graph_profile, vector_profile)
    if profile_has_data(profile):
        save_profile_snapshot(profile, db_path)
