from __future__ import annotations

from .connection import get_connection, DEFAULT_DB_PATH


def get_misc(db_path: str = DEFAULT_DB_PATH) -> dict:
    """The singleton misc record: {text, updated_at}. Empty text means 'nothing stored yet'."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT merged_text, updated_at FROM misc_context WHERE id = 'singleton'"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"text": "", "updated_at": ""}
    try:
        text, updated_at = row["merged_text"], row["updated_at"]
    except (KeyError, IndexError, TypeError):
        text, updated_at = row[0], row[1]
    return {"text": text or "", "updated_at": updated_at or ""}


def save_merged(merged_text: str, db_path: str = DEFAULT_DB_PATH) -> dict:
    """Replace the singleton with freshly merged text (merge itself happens in profile/misc_merger)."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO misc_context(id, merged_text, updated_at) VALUES('singleton', ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET merged_text = excluded.merged_text, updated_at = datetime('now')",
            (merged_text or "",),
        )
        conn.commit()
    finally:
        conn.close()
    return get_misc(db_path)


def clear_misc(db_path: str = DEFAULT_DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("UPDATE misc_context SET merged_text = '', updated_at = datetime('now') WHERE id = 'singleton'")
        conn.commit()
    finally:
        conn.close()
