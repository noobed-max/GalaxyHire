from __future__ import annotations

import json
from typing import Any
from .connection import get_connection

_TASK_SELECT = (
    "task_id, status, filename, file_path, tag_id, topic, stage, stage_number, "
    "stage_message, progress_percent, thoughts_json, duplicates_json, result_json, "
    "error, started_at, completed_at, created_at"
)


def _row_dict(row) -> dict[str, Any]:
    d = dict(row)
    if "thoughts_json" in d:
        try:
            d["thoughts"] = json.loads(d.pop("thoughts_json") or "[]")
        except Exception:
            d["thoughts"] = []
    if "duplicates_json" in d:
        try:
            d["duplicates"] = json.loads(d.pop("duplicates_json") or "[]")
        except Exception:
            d["duplicates"] = []
    if "result_json" in d:
        try:
            d["result"] = json.loads(d.pop("result_json") or "{}")
        except Exception:
            d["result"] = {}
    return d


def create_task_record(
    *,
    task_id: str,
    filename: str,
    file_path: str,
    tag_id: str | None = None,
    topic: str = "",
    stage: str = "reading",
    stage_number: int = 1,
    stage_message: str = "",
    progress_percent: int = 0,
    started_at: str | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO ingestion_tasks (
                task_id, status, filename, file_path, tag_id, topic,
                stage, stage_number, stage_message, progress_percent,
                thoughts_json, duplicates_json, result_json, started_at
            ) VALUES (?, 'processing', ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', '{}', COALESCE(?, datetime('now')))
            """,
            (
                task_id, filename, file_path, tag_id, topic,
                stage, stage_number, stage_message, progress_percent, started_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_task_record(task_id, db_path=db_path)  # type: ignore[return-value]


def update_task_record(
    task_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    stage_number: int | None = None,
    stage_message: str | None = None,
    progress_percent: int | None = None,
    thoughts: list[str] | None = None,
    duplicates: list[dict] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    completed_at: str | None = None,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    updates: list[str] = []
    params: list[Any] = []

    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if stage is not None:
        updates.append("stage = ?")
        params.append(stage)
    if stage_number is not None:
        updates.append("stage_number = ?")
        params.append(stage_number)
    if stage_message is not None:
        updates.append("stage_message = ?")
        params.append(stage_message)
    if progress_percent is not None:
        updates.append("progress_percent = ?")
        params.append(progress_percent)
    if thoughts is not None:
        updates.append("thoughts_json = ?")
        params.append(json.dumps(thoughts))
    if duplicates is not None:
        updates.append("duplicates_json = ?")
        params.append(json.dumps(duplicates))
    if result is not None:
        updates.append("result_json = ?")
        params.append(json.dumps(result))
    if error is not None:
        updates.append("error = ?")
        params.append(error)
    if completed_at is not None:
        updates.append("completed_at = ?")
        params.append(completed_at)

    if not updates:
        return get_task_record(task_id, db_path=db_path)

    params.append(task_id)
    conn = get_connection(db_path)
    try:
        conn.execute(
            f"UPDATE ingestion_tasks SET {', '.join(updates)} WHERE task_id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()
    return get_task_record(task_id, db_path=db_path)


def get_task_record(task_id: str, db_path: str | None = None) -> dict[str, Any] | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            f"SELECT {_TASK_SELECT} FROM ingestion_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_dict(row) if row else None


def get_latest_task_record(db_path: str | None = None) -> dict[str, Any] | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            f"SELECT {_TASK_SELECT} FROM ingestion_tasks ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return _row_dict(row) if row else None


def get_active_task_record(db_path: str | None = None) -> dict[str, Any] | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            f"SELECT {_TASK_SELECT} FROM ingestion_tasks WHERE status = 'processing' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return _row_dict(row) if row else None


def list_task_records(limit: int = 20, db_path: str | None = None) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_TASK_SELECT} FROM ingestion_tasks ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_dict(r) for r in rows]


_STAGED_DUP_SELECT = (
    "id, task_id, document_id, tag_id, parent_kind, parent_id, company_name, role, period, "
    "entity_title, existing_point_id, existing_text, new_text, explanation, status, "
    "created_at, resolved_at"
)


def create_staged_duplicates_batch(
    items: list[dict[str, Any]], db_path: str | None = None
) -> int:
    if not items:
        return 0
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN")
        count = 0
        for item in items:
            conn.execute(
                """
                INSERT OR REPLACE INTO staged_duplicates (
                    id, task_id, document_id, tag_id, parent_kind, parent_id,
                    company_name, role, period, entity_title,
                    existing_point_id, existing_text, new_text, explanation, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["task_id"],
                    item.get("document_id", ""),
                    item.get("tag_id"),
                    item["parent_kind"],
                    item["parent_id"],
                    item.get("company_name", ""),
                    item.get("role", ""),
                    item.get("period", ""),
                    item.get("entity_title", ""),
                    item["existing_point_id"],
                    item["existing_text"],
                    item["new_text"],
                    item.get("explanation", ""),
                    item.get("status", "pending"),
                ),
            )
            count += 1
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_staged_duplicates_for_task(
    task_id: str, status: str | None = None, db_path: str | None = None
) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        if status:
            rows = conn.execute(
                f"SELECT {_STAGED_DUP_SELECT} FROM staged_duplicates WHERE task_id = ? AND status = ? ORDER BY created_at ASC",
                (task_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_STAGED_DUP_SELECT} FROM staged_duplicates WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_staged_duplicates_grouped(
    task_id: str, db_path: str | None = None
) -> list[dict[str, Any]]:
    """Return pending staged duplicates grouped by company/project entity."""
    dups = get_staged_duplicates_for_task(task_id, status="pending", db_path=db_path)
    groups_map: dict[tuple[str, str], dict[str, Any]] = {}

    for d in dups:
        group_key = (d["parent_kind"], d["parent_id"])
        if group_key not in groups_map:
            groups_map[group_key] = {
                "company_name": d["company_name"],
                "role": d["role"],
                "period": d["period"],
                "parent_kind": d["parent_kind"],
                "parent_id": d["parent_id"],
                "entity_title": d["entity_title"] or d["company_name"],
                "pairs": [],
            }
        groups_map[group_key]["pairs"].append({
            "pair_id": d["id"],
            "existing_point_id": d["existing_point_id"],
            "existing_text": d["existing_text"],
            "new_text": d["new_text"],
            "explanation": d["explanation"],
        })

    return list(groups_map.values())


def get_staged_duplicate(
    duplicate_id: str, db_path: str | None = None
) -> dict[str, Any] | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            f"SELECT {_STAGED_DUP_SELECT} FROM staged_duplicates WHERE id = ?",
            (duplicate_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def update_staged_duplicate_status(
    duplicate_id: str,
    status: str,
    resolved_at: str | None = None,
    db_path: str | None = None,
) -> bool:
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            UPDATE staged_duplicates
            SET status = ?, resolved_at = COALESCE(?, datetime('now'))
            WHERE id = ?
            """,
            (status, resolved_at, duplicate_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def count_pending_staged_duplicates(
    task_id: str, db_path: str | None = None
) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM staged_duplicates WHERE task_id = ? AND status = 'pending'",
            (task_id,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["cnt"]) if row else 0
