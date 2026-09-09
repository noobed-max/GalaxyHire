# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vasudev Siddh and auto-devs
"""Integration and unit tests for Interactive Two-Column Duplicate Resolution System (Milestone 3).

Covers:
- Scoped duplicate detection in conflicts_detect.py: eliminates cross-company false positives, matches same-company variants
- SQLite migration 011_staged_duplicates.sql and staged duplicate CRUD helpers
- GET /api/v1/documents/ingest/{task_id}/duplicates (entity grouping by company/project)
- POST /api/v1/documents/ingest/{task_id}/resolve (actions: use_new, keep_both, keep_existing)
- Atomic graph/snapshot/point_tags mutations on resolution
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(__file__).resolve().parent / ".scratch-duplicates"


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


def test_scoped_duplicate_detection_prevents_cross_company_leaks():
    """Verify bullet points across different companies are NEVER paired as duplicates,
    while near-duplicate bullets under the SAME company ARE paired."""
    from data.conflicts_detect import detect

    def fake_embed(texts):
        # Maps texts containing 'cloud' or 'aws' to identical vectors
        return [[1.0, 0.0] if ("cloud" in t.lower() or "aws" in t.lower()) else [0.0, 1.0] for t in texts]

    # Bullets from different companies: parent_id 'comp-a' vs 'comp-b'
    cross_company_points = [
        {
            "kind": "experience",
            "id": "pt-1",
            "parent_id": "comp-a",
            "text": "Built cloud infrastructure on AWS",
            "header": "Engineer @ Company A",
        },
        {
            "kind": "experience",
            "id": "pt-2",
            "parent_id": "comp-b",
            "text": "Built cloud infrastructure on AWS with Terraform",
            "header": "Engineer @ Company B",
        },
    ]

    # Must NOT detect duplicates across different companies
    cross_groups = detect(cross_company_points, embed_fn=fake_embed)
    assert len(cross_groups) == 0, f"Cross-company false duplicate detected: {cross_groups}"

    # Same company: parent_id 'comp-a' for both
    same_company_points = [
        {
            "kind": "experience",
            "id": "pt-1",
            "parent_id": "comp-a",
            "text": "Built cloud infrastructure on AWS",
            "header": "Engineer @ Company A",
        },
        {
            "kind": "experience",
            "id": "pt-3",
            "parent_id": "comp-a",
            "text": "Built cloud infrastructure on AWS with Terraform",
            "header": "Engineer @ Company A",
        },
    ]

    same_groups = detect(same_company_points, embed_fn=fake_embed)
    assert len(same_groups) == 1, f"Expected same-company duplicate, got: {same_groups}"
    assert same_groups[0]["reason"] == "embedding"
    members = {m["id"] for m in same_groups[0]["members"]}
    assert members == {"pt-1", "pt-3"}


def test_staged_duplicates_persistence_crud():
    """Verify migration 011 and staged duplicates CRUD helper functions."""
    _run_script(
        """
from data.sqlite import ingestion_tasks as it
from data.sqlite.connection import get_connection

conn = get_connection(db)
migrations = [r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()]
conn.close()
assert "011_staged_duplicates.sql" in migrations, migrations

# Create task record first
it.create_task_record(
    task_id="task-dup-1",
    filename="resume_v2.pdf",
    file_path="/tmp/resume_v2.pdf",
    db_path=db,
)

# Batch insert staged duplicates
items = [
    {
        "id": "pair-1",
        "task_id": "task-dup-1",
        "document_id": "doc-1",
        "parent_kind": "experience",
        "parent_id": "exp-suprmntr",
        "company_name": "SuprMentr",
        "role": "Intern",
        "period": "Feb 2026 – May 2026",
        "entity_title": "SuprMentr (Intern · Feb 2026 – May 2026)",
        "existing_point_id": "exp_pt_1",
        "existing_text": "Built distributed backend on GCP k3s cluster.",
        "new_text": "Built distributed backend on GCP k3s with Longhorn storage.",
        "explanation": "Added Longhorn storage detail",
        "status": "pending",
    },
    {
        "id": "pair-2",
        "task_id": "task-dup-1",
        "document_id": "doc-1",
        "parent_kind": "project",
        "parent_id": "proj-garage",
        "company_name": "Garage S3",
        "role": "",
        "period": "",
        "entity_title": "Garage S3",
        "existing_point_id": "proj_pt_1",
        "existing_text": "Self-hosted S3-compatible object storage.",
        "new_text": "Self-hosted S3-compatible storage with distributed erasure coding.",
        "explanation": "Added erasure coding detail",
        "status": "pending",
    },
]

count = it.create_staged_duplicates_batch(items, db_path=db)
assert count == 2, count

# Test grouped query
groups = it.get_staged_duplicates_grouped("task-dup-1", db_path=db)
assert len(groups) == 2, groups
companies = {g["company_name"] for g in groups}
assert companies == {"SuprMentr", "Garage S3"}, companies

# Test single fetch
single = it.get_staged_duplicate("pair-1", db_path=db)
assert single is not None
assert single["existing_point_id"] == "exp_pt_1"
assert single["status"] == "pending"

# Test status update
ok = it.update_staged_duplicate_status("pair-1", "resolved_use_new", db_path=db)
assert ok is True
updated = it.get_staged_duplicate("pair-1", db_path=db)
assert updated["status"] == "resolved_use_new"
assert updated["resolved_at"] is not None

# Test pending count
remaining = it.count_pending_staged_duplicates("task-dup-1", db_path=db)
assert remaining == 1, remaining

print("STAGED_DUPLICATES_CRUD_OK")
"""
    )


def test_get_duplicates_endpoint():
    """Verify GET /api/v1/documents/ingest/{task_id}/duplicates contract."""
    _run_script(
        """
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routers.documents import create_router
from api.ingestion_tasks import reset_ingestion_task_manager
from data.sqlite import ingestion_tasks as it
from data.sqlite.connection import DEFAULT_DB_PATH
from core.logging import get_logger

logger = get_logger("test_dup_get")
reset_ingestion_task_manager()

app = FastAPI()
app.include_router(create_router(logger))
client = TestClient(app)

# Non-existent task returns 404
resp = client.get("/api/v1/documents/ingest/non-existent-task/duplicates")
assert resp.status_code == 404, resp.status_code

# Setup an ingestion task with staged duplicates in default DB
it.create_task_record(
    task_id="task-review-99",
    filename="engineer_resume.pdf",
    file_path="/tmp/engineer_resume.pdf",
    stage="completed",
    stage_number=4,
    db_path=DEFAULT_DB_PATH,
)
it.update_task_record("task-review-99", status="review_required", db_path=DEFAULT_DB_PATH)

# Add staged duplicate
it.create_staged_duplicates_batch([
    {
        "id": "pair-abc",
        "task_id": "task-review-99",
        "document_id": "doc-99",
        "parent_kind": "experience",
        "parent_id": "exp-1",
        "company_name": "Acme Corp",
        "role": "Lead Architect",
        "period": "2024 – Present",
        "entity_title": "Acme Corp (Lead Architect · 2024 – Present)",
        "existing_point_id": "pt-old-1",
        "existing_text": "Designed high-throughput Kafka streaming pipeline.",
        "new_text": "Architected low-latency Kafka stream processor handling 200k msg/s.",
        "explanation": "Expanded metrics and throughput",
        "status": "pending",
    }
], db_path=DEFAULT_DB_PATH)

resp = client.get("/api/v1/documents/ingest/task-review-99/duplicates")
assert resp.status_code == 200, resp.text
data = resp.json()

assert data["task_id"] == "task-review-99"
assert data["status"] == "review_required"
assert len(data["groups"]) == 1
group = data["groups"][0]
assert group["company_name"] == "Acme Corp"
assert group["role"] == "Lead Architect"
assert len(group["pairs"]) == 1
pair = group["pairs"][0]
assert pair["pair_id"] == "pair-abc"
assert pair["existing_point_id"] == "pt-old-1"
assert "Designed high-throughput" in pair["existing_text"]
assert "200k msg/s" in pair["new_text"]

print("GET_DUPLICATES_ENDPOINT_OK")
"""
    )


def test_resolve_actions_and_graph_mutations():
    """Verify POST /api/v1/documents/ingest/{task_id}/resolve executes:
    - 'use_new': replaces bullet text and migrates point_tags
    - 'keep_both': appends bullet and tags new point
    - 'keep_existing': discards bullet and tags existing point
    - task transitions to 'completed' when all duplicates resolved
    """
    _run_script(
        """
import asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routers.documents import create_router
from api.dependencies import get_profile_service
from api.ingestion_tasks import reset_ingestion_task_manager
from data.sqlite import ingestion_tasks as it
from data.sqlite import tags as t
from data.sqlite import point_tags as pt
from data.sqlite.connection import DEFAULT_DB_PATH
from data.graph import profile as graph_profile
from profile.ingest_parse import point_id
from core.logging import get_logger

logger = get_logger("test_resolve")
reset_ingestion_task_manager()

app = FastAPI()
app.include_router(create_router(logger))
client = TestClient(app)
profile_svc = get_profile_service()

# 1. Create tag
tag = t.create_tag("Backend", db_path=DEFAULT_DB_PATH)
tag_id = tag["id"]

# 2. Setup existing profile with experience & project
exp_res = profile_svc.add_experience(
    role="Backend Dev",
    company="MetaScale",
    period="2023 - 2025",
    description="Built payment service using Stripe APIs."
)
exp_id = exp_res["id"]
old_pt_id = point_id(exp_id, "Built payment service using Stripe APIs.")
# Associate old point with tag
pt.add_point_tags_batch([("experience", old_pt_id, tag_id)], db_path=DEFAULT_DB_PATH)

# Setup task and staged duplicate for use_new
it.create_task_record(task_id="task-res-1", filename="resume.pdf", file_path="/tmp/res.pdf", tag_id=tag_id, db_path=DEFAULT_DB_PATH)
it.update_task_record("task-res-1", status="review_required", db_path=DEFAULT_DB_PATH)

new_bullet_text = "Built PCI-compliant payment microservice handling $10M/day."
it.create_staged_duplicates_batch([
    {
        "id": "pair-use-new",
        "task_id": "task-res-1",
        "document_id": "doc-1",
        "tag_id": tag_id,
        "parent_kind": "experience",
        "parent_id": exp_id,
        "company_name": "MetaScale",
        "role": "Backend Dev",
        "period": "2023 - 2025",
        "entity_title": "MetaScale",
        "existing_point_id": old_pt_id,
        "existing_text": "Built payment service using Stripe APIs.",
        "new_text": new_bullet_text,
        "status": "pending",
    }
], db_path=DEFAULT_DB_PATH)

# Execute use_new resolution
resp = client.post(
    "/api/v1/documents/ingest/task-res-1/resolve",
    json={"resolutions": [{"pair_id": "pair-use-new", "action": "use_new"}]}
)
assert resp.status_code == 200, resp.text
res_data = resp.json()
assert res_data["status"] == "resolved"
assert res_data["resolved_count"] == 1
assert res_data["remaining_count"] == 0

# Check profile experience updated with new text
prof = profile_svc.get_profile()
exp_updated = next(e for e in prof["exp"] if e["id"] == exp_id)
assert new_bullet_text in (exp_updated.get("d") or exp_updated.get("description"))

# Check point_tags migrated to new_point_id
new_pt_id = point_id(exp_id, new_bullet_text)
tagged_points = pt.list_point_tags(db_path=DEFAULT_DB_PATH)
point_ids_tagged = {row["point_id"] for row in tagged_points if row["point_kind"] == "experience"}
assert new_pt_id in point_ids_tagged, f"New point {new_pt_id} not in tagged points {point_ids_tagged}"

# Check task status transitioned to completed
task_rec = it.get_task_record("task-res-1", db_path=DEFAULT_DB_PATH)
assert task_rec["status"] == "completed"

# 3. Test keep_both resolution
proj_res = profile_svc.add_project(
    title="CloudStore",
    stack="Go, S3",
    repo="github.com/cloudstore",
    impact="Implemented distributed file sync."
)
proj_id = proj_res["id"]
proj_old_pt_id = point_id(proj_id, "Implemented distributed file sync.")

it.create_task_record(task_id="task-res-2", filename="resume.pdf", file_path="/tmp/res.pdf", tag_id=tag_id, db_path=DEFAULT_DB_PATH)
it.update_task_record("task-res-2", status="review_required", db_path=DEFAULT_DB_PATH)

proj_new_text = "Implemented multi-region distributed file sync with conflict-free replication."
it.create_staged_duplicates_batch([
    {
        "id": "pair-keep-both",
        "task_id": "task-res-2",
        "document_id": "doc-2",
        "tag_id": tag_id,
        "parent_kind": "project",
        "parent_id": proj_id,
        "company_name": "CloudStore",
        "role": "",
        "period": "",
        "entity_title": "CloudStore",
        "existing_point_id": proj_old_pt_id,
        "existing_text": "Implemented distributed file sync.",
        "new_text": proj_new_text,
        "status": "pending",
    }
], db_path=DEFAULT_DB_PATH)

resp = client.post(
    "/api/v1/documents/ingest/task-res-2/resolve",
    json={"resolutions": [{"pair_id": "pair-keep-both", "action": "keep_both"}]}
)
assert resp.status_code == 200, resp.text

prof2 = profile_svc.get_profile()
proj_updated = next(p for p in prof2["projects"] if p["id"] == proj_id)
impact = proj_updated.get("impact") or ""
assert "Implemented distributed file sync." in impact
assert proj_new_text in impact

# 4. Test keep_existing resolution
it.create_task_record(task_id="task-res-3", filename="resume.pdf", file_path="/tmp/res.pdf", tag_id=tag_id, db_path=DEFAULT_DB_PATH)
it.update_task_record("task-res-3", status="review_required", db_path=DEFAULT_DB_PATH)

it.create_staged_duplicates_batch([
    {
        "id": "pair-keep-existing",
        "task_id": "task-res-3",
        "document_id": "doc-3",
        "tag_id": tag_id,
        "parent_kind": "experience",
        "parent_id": exp_id,
        "company_name": "MetaScale",
        "role": "Backend Dev",
        "period": "2023 - 2025",
        "entity_title": "MetaScale",
        "existing_point_id": new_pt_id,
        "existing_text": new_bullet_text,
        "new_text": "Another unwanted variant of payment bullet.",
        "status": "pending",
    }
], db_path=DEFAULT_DB_PATH)

resp = client.post(
    "/api/v1/documents/ingest/task-res-3/resolve",
    json={"resolutions": [{"pair_id": "pair-keep-existing", "action": "keep_existing"}]}
)
assert resp.status_code == 200, resp.text
prof3 = profile_svc.get_profile()
exp3 = next(e for e in prof3["exp"] if e["id"] == exp_id)
assert "Another unwanted variant" not in (exp3.get("d") or exp3.get("description"))

print("RESOLVE_ACTIONS_AND_GRAPH_MUTATIONS_OK")
"""
    )


def test_resolve_validation_errors():
    """Verify endpoint validation for invalid task IDs and unsupported actions."""
    _run_script(
        """
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routers.documents import create_router
from data.sqlite import ingestion_tasks as it
from data.sqlite.connection import DEFAULT_DB_PATH
from core.logging import get_logger

logger = get_logger("test_val")
app = FastAPI()
app.include_router(create_router(logger))
client = TestClient(app)

# 404 on invalid task ID
resp = client.post(
    "/api/v1/documents/ingest/unknown-task-id/resolve",
    json={"resolutions": [{"pair_id": "p1", "action": "use_new"}]}
)
assert resp.status_code == 404, resp.status_code

# Setup valid task
it.create_task_record(task_id="task-val-1", filename="resume.pdf", file_path="/tmp/res.pdf", db_path=DEFAULT_DB_PATH)

# 422 on invalid action
resp = client.post(
    "/api/v1/documents/ingest/task-val-1/resolve",
    json={"resolutions": [{"pair_id": "p1", "action": "invalid_action_name"}]}
)
assert resp.status_code == 422, resp.status_code

print("RESOLVE_VALIDATION_ERRORS_OK")
"""
    )
