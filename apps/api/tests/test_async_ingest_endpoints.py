# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vasudev Siddh and auto-devs
"""Integration and unit tests for Async Ingestion & Task Persistence (Milestone 1).

Covers:
- SQLite migration 010_ingestion_tasks.sql and task CRUD helpers
- Batch point_tags linking with INSERT OR IGNORE
- IngestionTaskManager fast in-memory cache and SQLite write-through
- POST /api/v1/documents/ingest (202 Accepted, file/raw validation, 409 concurrency lock)
- GET /api/v1/documents/ingest/status (idle contract, active task polling, auto-reconnect)
- POST /api/v1/documents/ingest/{task_id}/cancel
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(__file__).resolve().parent / ".scratch-async-ingest"


def _run_script(body: str, timeout: int = 40) -> str:
    SCRATCH.mkdir(exist_ok=True)
    run_dir = SCRATCH / f"run-{uuid.uuid4().hex}"
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(run_dir / f"test-{uuid.uuid4().hex}.db")
    script = (
        "import sys, os; sys.path.insert(0, '.');\n"
        f"os.environ['JHM_APP_DATA_DIR'] = {str(run_dir)!r};\n"
        "from data.sqlite.connection import init_sql, DEFAULT_DB_PATH;\n"
        f"db = {db_path!r};\n"
        "init_sql(db);\n"
        "init_sql();\n"
        + body
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        assert result.returncode == 0, f"Subprocess failed:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}"
        return result.stdout.strip()
    finally:
        for suffix in ("", "-wal", "-shm"):
            cand = Path(db_path + suffix)
            if cand.exists():
                try:
                    cand.unlink()
                except OSError:
                    pass


def test_sqlite_migration_and_task_crud():
    _run_script(
        """
from data.sqlite import ingestion_tasks as it
from data.sqlite.connection import get_connection

conn = get_connection(db)
migrations = [r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()]
conn.close()
assert "010_ingestion_tasks.sql" in migrations, migrations

# Create task record
rec = it.create_task_record(
    task_id="t-101",
    filename="my_resume.pdf",
    file_path="/tmp/my_resume.pdf",
    tag_id=None,
    topic="engineer",
    stage="reading",
    stage_number=1,
    stage_message="Reading document...",
    db_path=db,
)
assert rec["task_id"] == "t-101"
assert rec["status"] == "processing"
assert rec["stage"] == "reading"
assert rec["stage_number"] == 1

# Verify get_active_task_record
active = it.get_active_task_record(db_path=db)
assert active is not None
assert active["task_id"] == "t-101"

# Update task record
updated = it.update_task_record(
    "t-101",
    stage="extracting",
    stage_number=2,
    stage_message="Analyzing with AI...",
    progress_percent=50,
    thoughts=["Reading layout...", "Identified 3 roles"],
    db_path=db,
)
assert updated["stage"] == "extracting"
assert updated["stage_number"] == 2
assert len(updated["thoughts"]) == 2

# Complete task
done = it.update_task_record(
    "t-101",
    status="completed",
    stage="completed",
    stage_number=4,
    progress_percent=100,
    completed_at="2026-09-07T15:00:00Z",
    result={"roles": 3},
    db_path=db,
)
assert done["status"] == "completed"
assert done["result"] == {"roles": 3}

# No active task now
assert it.get_active_task_record(db_path=db) is None

# But latest task record still returns it
latest = it.get_latest_task_record(db_path=db)
assert latest["task_id"] == "t-101"
print("SQLITE_CRUD_OK")
"""
    )


def test_point_tags_batch_linking():
    _run_script(
        """
from data.sqlite import tags as t
from data.sqlite import point_tags as pt

tag = t.create_tag("Backend", db_path=db)
tag_id = tag["id"]

# Batch link multiple items
records = [
    ("skill", "skill-py", tag_id),
    ("experience", "exp-google", tag_id),
    ("project", "proj-ai", tag_id),
]
count = pt.add_point_tags_batch(records, db_path=db)
assert count == 3, count

# Link additional points using link_points_to_tag
count2 = pt.link_points_to_tag("skill", ["skill-go", "skill-rust"], tag_id, db_path=db)
assert count2 == 2, count2

# Re-inserting duplicates ignores without error
count_dupe = pt.add_point_tags_batch(records, db_path=db)
assert count_dupe == 0, count_dupe

all_pts = pt.list_point_tags(db_path=db)
assert len(all_pts) == 5, len(all_pts)
print("POINT_TAGS_BATCH_OK")
"""
    )


def test_ingestion_task_manager():
    _run_script(
        """
import asyncio
from api.ingestion_tasks import IngestionTaskManager

async def main():
    mgr = IngestionTaskManager(db_path=db)

    # 1. Create task
    task = await mgr.create_task(
        filename="alice_resume.pdf",
        file_path="/tmp/alice.pdf",
        tag_id=None,
        topic="Alice",
    )
    assert task.task_id
    assert task.status == "processing"
    assert task.stage == "reading"

    # 2. In-memory lookup
    fetched = await mgr.get_task(task.task_id)
    assert fetched is not None
    assert fetched.filename == "alice_resume.pdf"

    # 3. Append thought
    await mgr.append_thought(task.task_id, "Found 4 projects")
    assert "Found 4 projects" in fetched.thoughts

    # 4. Update stage
    await mgr.update_task(
        task.task_id,
        stage="deduping",
        stage_number=3,
        stage_message="Checking duplicates...",
    )
    assert fetched.stage == "deduping"
    assert fetched.stage_number == 3

    # 5. Active task query
    active = await mgr.get_active_or_latest_task()
    assert active is not None
    assert active.task_id == task.task_id

    # 6. Cancel task
    cancelled = await mgr.cancel_task(task.task_id)
    assert cancelled is True
    assert fetched.status == "cancelled"

    # 7. SQLite hydration: create new manager instance with empty cache
    mgr2 = IngestionTaskManager(db_path=db)
    hydrated = await mgr2.get_task(task.task_id)
    assert hydrated is not None
    assert hydrated.task_id == task.task_id
    assert hydrated.status == "cancelled"
    assert "Found 4 projects" in hydrated.thoughts
    print("TASK_MANAGER_OK")

asyncio.run(main())
"""
    )


def test_async_ingest_endpoints_via_testclient():
    _run_script(
        """
import asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routers.documents import create_router
from api.ingestion_tasks import get_ingestion_task_manager, reset_ingestion_task_manager
from core.logging import get_logger

logger = get_logger("test_async_ingest")
reset_ingestion_task_manager()

app = FastAPI()
app.include_router(create_router(logger))
client = TestClient(app)

# 1. Status query when idle
res = client.get("/api/v1/documents/ingest/status")
assert res.status_code == 200, res.text
data = res.json()
assert data["status"] == "idle"
assert data["task_id"] is None
assert data["stage_number"] == 0

# 2. Validation error when neither file nor raw is provided
res_bad = client.post("/api/v1/documents/ingest", data={"kind": "resume"})
assert res_bad.status_code == 400
assert "Either 'file' or 'raw'" in res_bad.json()["detail"]

# 3. Successful task registration with raw text (202 Accepted)
res_ingest = client.post(
    "/api/v1/documents/ingest",
    data={
        "kind": "resume",
        "raw": "Alice Developer\\nBuilt microservices in Go and Python.",
        "topic": "Alice CV",
    },
)
assert res_ingest.status_code == 202, res_ingest.text
job = res_ingest.json()
assert job["task_id"]
assert job["status"] == "processing"
assert job["filename"] == "Alice CV"
task_id = job["task_id"]

# 4. Concurrency lock: trying to start another task while one is processing -> 409 Conflict
res_conflict = client.post(
    "/api/v1/documents/ingest",
    data={"kind": "resume", "raw": "Bob Developer", "topic": "Bob CV"},
)
assert res_conflict.status_code == 409, res_conflict.text
assert "already in progress" in res_conflict.json()["detail"]

# 5. Status query with task_id
res_status = client.get(f"/api/v1/documents/ingest/status?task_id={task_id}")
assert res_status.status_code == 200
status_data = res_status.json()
assert status_data["task_id"] == task_id
assert status_data["status"] in ("processing", "completed", "failed")

# 6. Status query without task_id (auto-reconnect)
res_reconnect = client.get("/api/v1/documents/ingest/status")
assert res_reconnect.status_code == 200
assert res_reconnect.json()["task_id"] == task_id

# 7. Cancel task
res_cancel = client.post(f"/api/v1/documents/ingest/{task_id}/cancel")
assert res_cancel.status_code == 200
assert res_cancel.json()["status"] == "cancelled"

# Status after cancel
res_after = client.get(f"/api/v1/documents/ingest/status?task_id={task_id}")
assert res_after.json()["status"] == "cancelled"

# 8. Querying unknown task_id returns 404
res_404 = client.get("/api/v1/documents/ingest/status?task_id=nonexistent-uuid")
assert res_404.status_code == 404

print("FASTAPI_ENDPOINTS_OK")
"""
    )
