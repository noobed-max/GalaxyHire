from __future__ import annotations

import hashlib
import json

from .connection import get_connection, DEFAULT_DB_PATH


def _selection_id(job_id: str, tag_id: str, name: str = "") -> str:
    key = f"{job_id}|{tag_id}" if job_id else f"preset|{name or tag_id}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def get(job_id: str, tag_id: str, db_path: str = DEFAULT_DB_PATH) -> dict | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT selection_json FROM doc_selections WHERE job_id = ? AND tag_id = ?",
            (job_id or "", tag_id or ""),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row["selection_json"])
    except (TypeError, ValueError):
        return None


def upsert(job_id: str, tag_id: str, selection: dict, name: str = "",
           db_path: str = DEFAULT_DB_PATH) -> dict:
    """Save the selection for one (job, tag) scope. Re-saving overwrites the
    previous pick for the same scope (UNIQUE(job_id, tag_id))."""
    job_id = (job_id or "").strip()
    tag_id = (tag_id or "").strip()
    name = (name or "").strip()
    payload = json.dumps(selection or {}, ensure_ascii=False)
    row_id = _selection_id(job_id, tag_id, name)
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO doc_selections(id, job_id, tag_id, name, selection_json, updated_at)
            VALUES(?,?,?,?,?,datetime('now'))
            ON CONFLICT(job_id, tag_id) DO UPDATE SET
                id=excluded.id, name=excluded.name,
                selection_json=excluded.selection_json, updated_at=excluded.updated_at
            """,
            (row_id, job_id, tag_id, name, payload),
        )
        conn.commit()
        row = conn.execute(
            "SELECT updated_at FROM doc_selections WHERE job_id = ? AND tag_id = ?",
            (job_id, tag_id),
        ).fetchone()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True, "id": row_id, "updated_at": row["updated_at"] if row else ""}


def delete(job_id: str, tag_id: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM doc_selections WHERE job_id = ? AND tag_id = ?",
            (job_id or "", tag_id or ""),
        )
        conn.commit()
    finally:
        conn.close()
    return cur.rowcount > 0


def list_presets(db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, job_id, tag_id, name, selection_json, updated_at
            FROM doc_selections WHERE job_id = ''
            ORDER BY updated_at DESC, name COLLATE NOCASE
            """
        ).fetchall()
    finally:
        conn.close()
    presets = []
    for row in rows:
        d = dict(row)
        try:
            d["selection"] = json.loads(d.pop("selection_json"))
        except (TypeError, ValueError):
            d["selection"] = {}
        presets.append(d)
    return presets


def save_preset(name: str, tag_id: str, selection: dict,
                db_path: str = DEFAULT_DB_PATH) -> dict:
    """Store the current selection as a named preset. Preset ids derive from the
    name; the table's UNIQUE(job_id, tag_id) also means one preset per tag
    scope, so an earlier preset on the same tag is explicitly replaced here
    instead of raising an IntegrityError."""
    name = (name or "").strip()
    if not name:
        raise ValueError("preset name is required")
    tag_id = (tag_id or "").strip()
    payload = json.dumps(selection or {}, ensure_ascii=False)
    row_id = _selection_id("", tag_id, name)
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN")
        # FK enforcement is off in this codebase; clear conflicting scopes so a
        # same-tag preset can never wedge the insert.
        conn.execute("DELETE FROM doc_selections WHERE id = ?", (row_id,))
        conn.execute(
            "DELETE FROM doc_selections WHERE job_id = '' AND tag_id = ? AND id != ?",
            (tag_id, row_id),
        )
        conn.execute(
            """
            INSERT INTO doc_selections(id, job_id, tag_id, name, selection_json, updated_at)
            VALUES(?,?,?,?,?,datetime('now'))
            """,
            (row_id, "", tag_id, name, payload),
        )
        conn.commit()
        row = conn.execute(
            "SELECT updated_at FROM doc_selections WHERE id = ?", (row_id,)
        ).fetchone()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True, "id": row_id, "name": name, "tag_id": tag_id,
            "selection": selection or {}, "updated_at": row["updated_at"] if row else ""}


def delete_preset(preset_id: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM doc_selections WHERE id = ? AND job_id = ''",
            (preset_id or "",),
        )
        conn.commit()
    finally:
        conn.close()
    return cur.rowcount > 0
