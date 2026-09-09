from __future__ import annotations

from .connection import get_connection

POINT_KINDS = ("skill", "project", "experience")


def list_point_tags(db_path: str | None = None) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT pt.point_kind, pt.point_id, pt.tag_id, t.name AS tag_name
            FROM point_tags pt JOIN tags t ON t.id = pt.tag_id
            ORDER BY pt.point_kind, pt.point_id, t.name COLLATE NOCASE
            """
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def set_point_tags(point_kind: str, point_id: str, tag_ids: list[str],
                   db_path: str | None = None) -> list[dict]:
    """Replace the full tag set for one profile point (chip-toggle semantics:
    the UI sends the complete wanted list). Unknown kinds raise; unknown tag
    ids are ignored so stale UI state can never resurrect deleted tags."""
    if point_kind not in POINT_KINDS:
        raise ValueError(f"point_kind must be one of {POINT_KINDS}")
    point_id = (point_id or "").strip()
    if not point_id:
        raise ValueError("point_id is required")
    clean_ids: list[str] = []
    for tag_id in tag_ids or []:
        tag_id = (tag_id or "").strip()
        if tag_id and tag_id not in clean_ids:
            clean_ids.append(tag_id)
    conn = get_connection(db_path)
    try:
        # FK enforcement is off in this codebase; drop unknown tag ids here so
        # no dangling rows can ever leak into scoping decisions.
        known: set[str] = set()
        for i in range(0, len(clean_ids), 100):
            chunk = clean_ids[i:i + 100]
            placeholders = ",".join("?" * len(chunk))
            known.update(r[0] for r in conn.execute(
                f"SELECT id FROM tags WHERE id IN ({placeholders})", chunk
            ).fetchall())
        clean_ids = [tag_id for tag_id in clean_ids if tag_id in known]
        conn.execute("BEGIN")
        conn.execute(
            "DELETE FROM point_tags WHERE point_kind = ? AND point_id = ?",
            (point_kind, point_id),
        )
        for tag_id in clean_ids:
            conn.execute(
                "INSERT OR IGNORE INTO point_tags(point_kind, point_id, tag_id) VALUES(?,?,?)",
                (point_kind, point_id, tag_id),
            )
        conn.commit()
        rows = conn.execute(
            """
            SELECT pt.point_kind, pt.point_id, pt.tag_id, t.name AS tag_name
            FROM point_tags pt JOIN tags t ON t.id = pt.tag_id
            WHERE pt.point_kind = ? AND pt.point_id = ?
            ORDER BY t.name COLLATE NOCASE
            """,
            (point_kind, point_id),
        ).fetchall()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return [dict(row) for row in rows]


def delete_for_tag(tag_id: str, db_path: str | None = None) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM point_tags WHERE tag_id = ?", (tag_id,))
        conn.commit()
    finally:
        conn.close()


def scoped_point_ids(tag_id: str, db_path: str | None = None) -> dict[str, set[str]]:
    """For a selected tag, return the ids to EXCLUDE per point kind.

    Semantics mirror the master-superset philosophy: points carrying NO tag are
    universal and always included; a point tagged with the chosen tag is in;
    a point tagged only with OTHER tags is scoped out. Points tagged with both
    the chosen tag and others are still included (multi-track membership)."""
    if not tag_id:
        return {}
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT point_kind, point_id, tag_id FROM point_tags"
        ).fetchall()
    finally:
        conn.close()
    tagged: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        d = dict(row)
        tagged.setdefault(d["point_kind"], {}).setdefault(d["point_id"], set()).add(d["tag_id"])
    excluded: dict[str, set[str]] = {}
    for kind, per_point in tagged.items():
        out: set[str] = set()
        for point_id, tag_ids in per_point.items():
            if tag_id not in tag_ids:
                out.add(point_id)
        if out:
            excluded[kind] = out
    return excluded


def add_point_tags_batch(
    records: list[tuple[str, str, str]], db_path: str | None = None
) -> int:
    """Batch-insert multiple point-to-tag mappings using INSERT OR IGNORE.
    records is a list of (point_kind, point_id, tag_id).

    Guarantees:
    1. Validates point_kind in POINT_KINDS.
    2. Validates tag_id against tags table (supporting id or name).
    3. Idempotently inserts into point_tags without wiping prior tags.
    4. Returns count of valid items processed.
    """
    if not records:
        return 0
    clean_items: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in records:
        if not (isinstance(item, (tuple, list)) and len(item) == 3):
            continue
        kind = str(item[0] or "").strip()
        pid = str(item[1] or "").strip()
        tid = str(item[2] or "").strip()
        if kind in POINT_KINDS and pid and tid:
            triple = (kind, pid, tid)
            if triple not in seen:
                seen.add(triple)
                clean_items.append(triple)

    if not clean_items:
        return 0

    conn = get_connection(db_path)
    try:
        raw_tag_keys = list({tid for _, _, tid in clean_items})
        tag_map: dict[str, str] = {}
        for i in range(0, len(raw_tag_keys), 100):
            chunk = raw_tag_keys[i:i + 100]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT id, name FROM tags WHERE id IN ({placeholders}) OR name IN ({placeholders}) COLLATE NOCASE",
                chunk + chunk,
            ).fetchall()
            for r in rows:
                tag_map[r["id"]] = r["id"]
                tag_map[r["name"].lower()] = r["id"]

        valid_items = []
        for kind, pid, tid in clean_items:
            resolved_tid = tag_map.get(tid) or tag_map.get(tid.lower())
            if resolved_tid:
                valid_items.append((kind, pid, resolved_tid))

        if not valid_items:
            return 0

        conn.execute("BEGIN")
        before_changes = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO point_tags(point_kind, point_id, tag_id) VALUES(?, ?, ?)",
            valid_items,
        )
        inserted = conn.total_changes - before_changes
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def link_points_to_tag(
    point_kind: str,
    point_ids: list[str],
    tag_id: str,
    db_path: str | None = None,
) -> int:
    """Link multiple point IDs of the same kind to tag_id with INSERT OR IGNORE."""
    if not tag_id or not point_ids:
        return 0
    records = [(point_kind, pid, tag_id) for pid in point_ids if pid]
    return add_point_tags_batch(records, db_path=db_path)


def add_tag_to_point(
    point_kind: str,
    point_id: str,
    tag_id: str,
    db_path: str | None = None,
) -> None:
    """Convenience helper to link a single point to tag_id."""
    if not (point_kind and point_id and tag_id):
        return
    add_point_tags_batch([(point_kind, point_id, tag_id)], db_path=db_path)


def reconcile_tagged_resume_document(
    document: dict | str,
    *,
    profile: dict | None = None,
    db_path: str | None = None,
) -> dict:
    """Repair one tagged resume from its stored excerpt (no LLM call).

    Kept as a data-layer entry point so API workers and maintenance jobs can
    invoke reconciliation without knowing the provenance module's location.
    The import is lazy to keep SQLite helpers usable during app startup.
    """
    from importlib import import_module

    reconcile = import_module("profile.provenance").reconcile_tagged_resume_document

    return reconcile(document, profile=profile, db_path=db_path)


def reconcile_tagged_resume_documents(
    *,
    document_id: str | None = None,
    tag_id: str | None = None,
    profile: dict | None = None,
    db_path: str | None = None,
) -> dict:
    """Repair tagged resume provenance and return counts/status for the caller."""
    from importlib import import_module

    reconcile = import_module("profile.provenance").reconcile_tagged_resume_documents

    return reconcile(
        document_id=document_id,
        tag_id=tag_id,
        profile=profile,
        db_path=db_path,
    )


backfill_tagged_resume_documents = reconcile_tagged_resume_documents
repair_tagged_resume_documents = reconcile_tagged_resume_documents
