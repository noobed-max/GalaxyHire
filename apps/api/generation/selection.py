from __future__ import annotations

from importlib import import_module

from core.logging import get_logger

_log = get_logger(__name__)

# Selection keys that carry user-picked ids; a selection touching none of them
# is treated as empty (no-op).
_ID_KEYS = ("skills_on", "experience_on", "projects_on", "points_on")

_SECTIONS = (
    # (profile key, entry-toggle key, order key, description-blob key)
    ("exp", "experience_on", "experience_order", "d"),
    ("projects", "projects_on", "projects_order", "impact"),
)


def is_empty_selection(selection: dict | None) -> bool:
    if not isinstance(selection, dict):
        return True
    return not any(selection.get(key) for key in _ID_KEYS)


def split_blob_points(parent_id: str, text: str) -> list[dict]:
    """[{id,text}] children derived from a description blob. Delegates to the
    profile package's own algorithm so ids always match what the editor showed
    (single source of truth); runtime import keeps the generation->profile
    boundary (test_import_boundaries)."""
    try:
        points = import_module("profile.ingest_parse").points_with_ids(parent_id, str(text or ""))
        return [p for p in points if isinstance(p, dict) and p.get("id") and p.get("text")]
    except Exception as exc:
        _log.debug("point split unavailable for %s: %s", parent_id, exc)
        return []


def _row_points(row: dict, text_key: str) -> list[dict]:
    """Points already carried by a GET /profile row, derived from its blob when
    missing (legacy rows)."""
    points = row.get("points")
    if isinstance(points, list) and points:
        return [p for p in points if isinstance(p, dict) and p.get("id")]
    row_id = str(row.get("id") or "")
    if not row_id:
        return []
    return split_blob_points(row_id, str(row.get(text_key) or ""))


def _unique(ids: list) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in ids or []:
        rid = str(raw or "")
        if rid and rid not in seen:
            seen.add(rid)
            out.append(rid)
    return out


def picked_points_by_row(rows: list[dict], points_on: list[str], text_key: str) -> dict[str, list[dict]]:
    """Map row id -> picked point dicts, ordered as the user ordered them.
    Membership is decided by matching against each row's OWN derived points:
    point ids carry no parent field, so this is the only reliable grouping."""
    wanted = _unique(points_on)
    if not wanted:
        return {}
    by_row: dict[str, list[dict]] = {}
    for row in rows or []:
        row_id = str(row.get("id") or "")
        if not row_id:
            continue
        known = {p["id"]: p for p in _row_points(row, text_key)}
        picked = [known[pid] for pid in wanted if pid in known]
        if picked:
            by_row[row_id] = picked
    return by_row


def _order_rows(rows: list[dict], order: list[str]) -> list[dict]:
    positions = {rid: idx for idx, rid in enumerate(_unique(order))}
    if not positions:
        return rows
    return sorted(rows, key=lambda row: positions.get(str(row.get("id") or ""), len(positions)))


def apply_selection(profile: dict, selection: dict | None, repo=None) -> tuple[dict, int]:
    """Narrow a (tag-scoped) profile to exactly the user's hand-picked content.

    Strictly WYSIWYG: once ANY ids are picked, only picked content survives —
    sections the user left untouched are excluded too, so what the pane showed
    is what gets generated. Returns (scoped_profile, dropped) where dropped
    counts requested ids that matched nothing (stale picks after a re-import);
    unknown ids are dropped silently and must never resurrect deleted content.
    An empty/None selection is a no-op: the tag-scoped superset flows through."""
    selection = selection or {}
    title_choices = {
        str(key): str(value).strip()
        for key, value in (selection.get("entity_titles") or {}).items()
        if str(key) and str(value).strip()
    }

    def apply_titles(source: dict) -> dict:
        if not title_choices:
            return source
        updated = dict(source)
        for section, field in (("exp", "role"), ("projects", "title")):
            updated[section] = [
                {**row, field: title_choices.get(str(row.get("id") or ""), str(row.get(field) or ""))}
                if isinstance(row, dict) else row
                for row in source.get(section, []) or []
            ]
        return updated

    profile = apply_titles(profile)
    if is_empty_selection(selection):
        return profile, 0
    scoped = dict(profile)
    dropped = 0

    rows = [row for row in (profile.get("skills") or []) if isinstance(row, dict)]
    skills_on = _unique(selection.get("skills_on"))
    matched = {str(row.get("id") or "") for row in rows} & set(skills_on)
    scoped["skills"] = [row for row in rows if str(row.get("id") or "") in matched]
    dropped += len(skills_on) - len(matched)

    all_picks: set[str] = set()
    for section_key, on_key, order_key, text_key in _SECTIONS:
        rows = [row for row in (profile.get(section_key) or []) if isinstance(row, dict)]
        picks = picked_points_by_row(rows, selection.get("points_on") or [], text_key)
        all_picks.update(pid for picked in picks.values() for pid in (p["id"] for p in picked))
        toggled = _unique(selection.get(on_key))
        on_set = set(toggled)
        dropped += len([rid for rid in toggled if rid not in {str(r.get("id") or "") for r in rows}])
        kept: list[dict] = []
        for row in rows:
            row_id = str(row.get("id") or "")
            if row_id not in on_set and row_id not in picks:
                continue
            new_row = dict(row)
            if row_id in picks:
                # Picked points only, kept in the user's order.
                new_row["points"] = picks[row_id]
            # Entry toggled on without specific point picks: keep ALL of its
            # points (header-checkbox semantics).
            kept.append(new_row)
        scoped[section_key] = _order_rows(kept, selection.get(order_key) or [])

    dropped += len(_unique(selection.get("points_on"))) - len(all_picks)
    return scoped, max(0, dropped)


def build_selection_block(profile: dict) -> str:
    """The binding prompt block built from the RESOLVED profile: whatever
    survived scoping + selection IS the pick list, with its points verbatim."""
    lines: list[str] = []
    projects = [row for row in (profile.get("projects") or []) if isinstance(row, dict)]
    if projects:
        lines.append("PROJECTS:")
        for row in projects:
            head = str(row.get("title") or "").strip() or "Untitled project"
            stack = row.get("stack")
            meta = ", ".join(stack) if isinstance(stack, list) else str(stack or "").strip()
            if meta:
                head = f"{head} [{meta}]"
            lines.append(f"### {head}")
            for point in _row_points(row, "impact"):
                lines.append(f"- {point['text']}")
    exp = [row for row in (profile.get("exp") or []) if isinstance(row, dict)]
    if exp:
        lines.append("EXPERIENCE:")
        for row in exp:
            head = " - ".join(part for part in (str(row.get("role") or "").strip(), str(row.get("co") or "").strip()) if part) or "Role"
            period = str(row.get("period") or "").strip()
            if period:
                head = f"{head} {period}"
            lines.append(f"### {head}")
            for point in _row_points(row, "d"):
                lines.append(f"- {point['text']}")
    skills = [str(s.get("n") or s.get("name") or "").strip()
              for s in (profile.get("skills") or []) if isinstance(s, dict)]
    skills = [s for s in skills if s]
    if skills:
        lines.append(f"SKILLS (include exactly these): {', '.join(skills)}")
    return "\n".join(lines).strip()
