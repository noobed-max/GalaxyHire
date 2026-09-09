from __future__ import annotations

import uuid

from .connection import get_connection


def list_tags(db_path: str | None = None) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, name, created_at FROM tags ORDER BY name COLLATE NOCASE"
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def create_tag(name: str, db_path: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("tag name is required")
    conn = get_connection(db_path)
    try:
        existing = conn.execute("SELECT id, name FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        if existing:
            return dict(existing)
        tag_id = uuid.uuid4().hex[:12]
        conn.execute("INSERT INTO tags(id, name) VALUES(?, ?)", (tag_id, name))
        conn.commit()
        row = conn.execute("SELECT id, name, created_at FROM tags WHERE id = ?", (tag_id,)).fetchone()
    finally:
        conn.close()
    return dict(row)


def delete_tag(tag_id: str, db_path: str | None = None) -> bool:
    conn = get_connection(db_path)
    try:
        cur = conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        # ON DELETE CASCADE is declared but FK enforcement is off here, so the
        # bullet-level point-tag rows are cleaned up explicitly.
        try:
            conn.execute("DELETE FROM point_tags WHERE tag_id = ?", (tag_id,))
        except Exception:
            pass  # point_tags table may predate this migration
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
