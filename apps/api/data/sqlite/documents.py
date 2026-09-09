from __future__ import annotations

import os
import json
import uuid

from .connection import get_connection

_DOC_SELECT = (
    "id, kind, topic, tag_id, file_path, source_filename, mime, excerpt, source, created_at"
)


def _row_dict(row) -> dict:
    d = dict(row)
    d["exists"] = os.path.isfile(d.get("file_path") or "")
    return d


def list_documents(kind: str | None = None, tag_id: str | None = None,
                   db_path: str | None = None) -> list[dict]:
    conn = get_connection(db_path)
    try:
        sql = f"SELECT {_DOC_SELECT} FROM documents WHERE 1=1"
        params: list = []
        if kind in {"resume", "cover_letter"}:
            sql += " AND kind = ?"
            params.append(kind)
        if tag_id:
            sql += " AND tag_id = ?"
            params.append(tag_id)
        sql += " ORDER BY created_at DESC, id DESC"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_row_dict(r) for r in rows]


def get_document(doc_id: str, db_path: str | None = None) -> dict | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            f"SELECT {_DOC_SELECT} FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_dict(row) if row else None


def create_document(
    *,
    kind: str,
    file_path: str,
    topic: str = "",
    tag_id: str | None = None,
    source_filename: str = "",
    mime: str = "",
    excerpt: str = "",
    source: str = "upload",
    resume_id: str = "",
    parsed_profile: dict | None = None,
    db_path: str | None = None,
) -> dict:
    if kind not in {"resume", "cover_letter"}:
        raise ValueError("kind must be 'resume' or 'cover_letter'")
    doc_id = uuid.uuid4().hex[:12]
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO documents(id, kind, topic, tag_id, file_path, source_filename,
                                  mime, excerpt, source, resume_id, parsed_profile_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (doc_id, kind, topic.strip(), tag_id, file_path, source_filename,
             mime, (excerpt or "")[:8000], source, str(resume_id or "")[:160],
             json.dumps(parsed_profile or {}, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return get_document(doc_id, db_path)  # type: ignore[return-value]


def get_parsed_profile(doc_id: str, db_path: str | None = None) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT resume_id, parsed_profile_json FROM documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    try:
        parsed = json.loads(row["parsed_profile_json"] or "{}")
    except (TypeError, ValueError):
        parsed = {}
    return {
        "resume_id": str(row["resume_id"] or ""),
        "profile": parsed if isinstance(parsed, dict) else {},
    }


def update_document(doc_id: str, topic: str | None = None, tag_id: str | None = None,
                    db_path: str | None = None) -> dict | None:
    conn = get_connection(db_path)
    try:
        if topic is not None:
            conn.execute("UPDATE documents SET topic = ? WHERE id = ?", (topic.strip(), doc_id))
        if tag_id is not None:
            conn.execute("UPDATE documents SET tag_id = ? WHERE id = ?", (tag_id, doc_id))
        conn.commit()
        row = conn.execute(
            f"SELECT {_DOC_SELECT} FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_dict(row) if row else None


def delete_document(doc_id: str, db_path: str | None = None) -> dict | None:
    """Remove the registry row only. The underlying profile points are shared
    graph data and stay — deleting one resume never touches another's data."""
    doc = get_document(doc_id, db_path)
    if not doc:
        return None
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.commit()
    finally:
        conn.close()
    return doc


def latest_for_tag(kind: str, tag_id: str, db_path: str | None = None) -> dict | None:
    docs = list_documents(kind=kind, tag_id=tag_id, db_path=db_path)
    return docs[0] if docs else None
