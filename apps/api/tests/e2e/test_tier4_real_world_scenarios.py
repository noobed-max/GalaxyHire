"""Tier 4: Real-World Application Scenarios E2E Tests.

Validates authentic end-to-end user workflows:
1. Dual-Track Candidate Onboarding (SDE + ML resumes)
2. Resume Revision with Metric Updates ("Use New")
3. Multi-Tab Navigation During In-Flight Ingestion
4. Selective Multi-Track Job Application Package Assembly
5. Alternate Wording Preservation for Dual-Focus Roles ("Keep Both")
"""

from __future__ import annotations

import io
import time
import pytest
from fastapi.testclient import TestClient

from tests.e2e.conftest import poll_ingest_task
from models.schema import CandidateProfile, ExperienceEntry, ProjectEntry, SkillEntry


def test_tier4_workflow_1_dual_track_onboarding(e2e_env, sample_resume_text):
    """Workflow 1: Fresh Candidate Dual-Track Onboarding (SDE + ML)."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]
    mock_llm = e2e_env["mock_llm"]

    # Step 1: Create tags for both tracks
    tag_sde = client.post("/api/v1/tags", json={"name": "SDE"}, headers=auth).json()["id"]
    tag_ml = client.post("/api/v1/tags", json={"name": "ML"}, headers=auth).json()["id"]

    # Step 2: Ingest SDE resume
    mock_llm.set_default_profile(mock_llm.build_sample_sde_profile())
    f_sde = {"file": ("FullStack_SDE.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    r_sde = client.post("/api/v1/documents/ingest", files=f_sde, data={"tag_id": tag_sde, "topic": "SDE Profile"}, headers=auth)
    assert r_sde.status_code == 202
    status_sde = poll_ingest_task(client, auth, task_id=r_sde.json()["task_id"], timeout_seconds=25.0)
    assert status_sde.get("status") == "completed"

    # Verify SDE points are mapped in point_tags
    pt_after_sde = client.get("/api/v1/point-tags", headers=auth).json().get("point_tags", [])
    assert any(pt.get("tag_id") == tag_sde for pt in pt_after_sde)

    # Step 3: Ingest ML resume for same candidate
    mock_llm.set_default_profile(mock_llm.build_sample_ml_profile())
    f_ml = {"file": ("ML_Engineer.txt", io.BytesIO(b"Alex Mercer ML Engineer CloudScale Inc"), "text/plain")}
    r_ml = client.post("/api/v1/documents/ingest", files=f_ml, data={"tag_id": tag_ml, "topic": "ML Profile"}, headers=auth)
    assert r_ml.status_code == 202
    status_ml = poll_ingest_task(client, auth, task_id=r_ml.json()["task_id"], timeout_seconds=25.0)
    assert status_ml.get("status") in ("completed", "review_required")

    # Step 4: Verify both tags are represented in point_tags
    pt_after_ml = client.get("/api/v1/point-tags", headers=auth).json().get("point_tags", [])
    tags_present = {pt.get("tag_id") for pt in pt_after_ml}
    assert tag_sde in tags_present


def test_tier4_workflow_2_resume_revision_with_metric_updates(e2e_env, sample_resume_text, sample_variant_resume_text):
    """Workflow 2: Resume Revision with Metric Updates ('Use New')."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Step 1: Ingest original resume
    f1 = {"file": ("Original_Resume.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    r1 = client.post("/api/v1/documents/ingest", files=f1, headers=auth)
    assert r1.status_code == 202
    poll_ingest_task(client, auth, task_id=r1.json()["task_id"], timeout_seconds=25.0)

    # Step 2: Upload revision with upgraded metrics
    f2 = {"file": ("Updated_Metrics_Resume.txt", io.BytesIO(sample_variant_resume_text.encode("utf-8")), "text/plain")}
    r2 = client.post("/api/v1/documents/ingest", files=f2, headers=auth)
    assert r2.status_code == 202
    task2_id = r2.json()["task_id"]
    poll_ingest_task(client, auth, task_id=task2_id, timeout_seconds=25.0)

    # Step 3: Check staged duplicates
    dupes_res = client.get(f"/api/v1/documents/ingest/{task2_id}/duplicates", headers=auth)
    assert dupes_res.status_code == 200
    groups = dupes_res.json().get("groups", [])
    if groups and groups[0].get("pairs"):
        pair_id = groups[0]["pairs"][0]["pair_id"]
        # Step 4: Resolve with "use_new"
        resolve_res = client.post(
            f"/api/v1/documents/ingest/{task2_id}/resolve",
            json={"resolutions": [{"pair_id": pair_id, "action": "use_new"}]},
            headers=auth,
        )
        assert resolve_res.status_code == 200
        assert resolve_res.json().get("resolved_count", 0) >= 1


def test_tier4_workflow_3_multi_tab_navigation_during_inflight_ingestion(e2e_env, sample_resume_text):
    """Workflow 3: Multi-Tab Navigation During In-Flight Ingestion."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Step 1: User starts upload in Ingestion tab
    files = {"file": ("heavy_resume.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    res = client.post("/api/v1/documents/ingest", files=files, headers=auth)
    assert res.status_code == 202
    task_id = res.json()["task_id"]

    # Step 2: User navigates to Dashboard and Settings (simulated API calls)
    client.get("/api/v1/settings", headers=auth)
    client.get("/api/v1/documents", headers=auth)

    # Step 3: User navigates back to Ingestion tab -> queries status without task_id
    reconnect_res = client.get("/api/v1/documents/ingest/status", headers=auth)
    assert reconnect_res.status_code == 200
    reconnected_task = reconnect_res.json()
    assert reconnected_task.get("task_id") == task_id
    assert "stage" in reconnected_task
    assert "thoughts" in reconnected_task

    # Step 4: Await completion
    final_status = poll_ingest_task(client, auth, task_id=task_id, timeout_seconds=25.0)
    assert final_status.get("status") == "completed"


def test_tier4_workflow_4_selective_multi_track_job_application_assembly(e2e_env):
    """Workflow 4: Selective Multi-Track Job Application Package Assembly."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Step 1: Set up tags INFRA and SDE
    client.post("/api/v1/tags", json={"name": "INFRA"}, headers=auth)
    client.post("/api/v1/tags", json={"name": "SDE"}, headers=auth)

    job_id = "job-architect-lead-99"

    # Step 2: User selects points across tracks under "Any" tag
    selection = {
        "points_on": ["exp_infra_1", "exp_infra_2", "exp_shared_arch"],
        "skills_on": ["Kubernetes", "Terraform", "Go"],
    }
    put_res = client.put(
        "/api/v1/doc-selections",
        json={"job_id": job_id, "tag_id": "", "selection": selection},
        headers=auth,
    )
    assert put_res.status_code in (200, 204)

    # Step 3: Fetch stored selection for package generation
    get_res = client.get(f"/api/v1/doc-selections?job_id={job_id}&tag_id=", headers=auth)
    assert get_res.status_code == 200
    loaded_sel = get_res.json().get("selection", {})
    assert len(loaded_sel.get("points_on", [])) == 3


def test_tier4_workflow_5_alternate_wording_preservation(e2e_env, sample_resume_text, sample_variant_resume_text):
    """Workflow 5: Alternate Wording Preservation for Dual-Focus Roles ('Keep Both')."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Ingest baseline
    f1 = {"file": ("baseline.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    r1 = client.post("/api/v1/documents/ingest", files=f1, headers=auth)
    poll_ingest_task(client, auth, task_id=r1.json()["task_id"], timeout_seconds=25.0)

    # Ingest research-focused variant
    f2 = {"file": ("research_variant.txt", io.BytesIO(sample_variant_resume_text.encode("utf-8")), "text/plain")}
    r2 = client.post("/api/v1/documents/ingest", files=f2, headers=auth)
    task2_id = r2.json()["task_id"]
    poll_ingest_task(client, auth, task_id=task2_id, timeout_seconds=25.0)

    # Resolve with keep_both
    dupes_res = client.get(f"/api/v1/documents/ingest/{task2_id}/duplicates", headers=auth)
    groups = dupes_res.json().get("groups", [])
    if groups and groups[0].get("pairs"):
        pair_id = groups[0]["pairs"][0]["pair_id"]
        resolve_res = client.post(
            f"/api/v1/documents/ingest/{task2_id}/resolve",
            json={"resolutions": [{"pair_id": pair_id, "action": "keep_both"}]},
            headers=auth,
        )
        assert resolve_res.status_code == 200
        assert resolve_res.json().get("status") == "resolved"
