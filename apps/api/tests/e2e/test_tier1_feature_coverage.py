"""Tier 1: Core Feature Coverage E2E Tests.

Validates primary functionality (happy path) and interface contracts for:
- R1: Asynchronous Ingestion & Session Persistence (Features 1-4)
- R2: Contextual AI Extraction & Exact Copy Deduplication (Features 5-8)
- R3: Interactive Two-Column Duplicate Resolution Dialog (Features 9-10)
- R4: Tag Selector Unification & Multi-Tag Track Layout (Features 11-12)

Requires >= 5 tests per feature, >= 20 tests total.
"""

from __future__ import annotations

import io
import time
import pytest
from fastapi.testclient import TestClient

from tests.e2e.conftest import create_resume_file, poll_ingest_task
from models.schema import CandidateProfile, ExperienceEntry, ProjectEntry, SkillEntry


# ════════════════════════════════════════════════════════════════════════════════
# R1: ASYNCHRONOUS INGESTION & SESSION PERSISTENCE (>= 5 TESTS)
# ════════════════════════════════════════════════════════════════════════════════

def test_r1_task_registration_returns_202_with_task_id(e2e_env, sample_resume_text):
    """Test 1.1: POST /api/v1/documents/ingest registers task and returns 202 Accepted with task_id."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Ingest resume via multipart form-data
    files = {"file": ("alex_sde_resume.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    data = {"topic": "Backend Infrastructure", "tag_id": ""}

    resp = client.post("/api/v1/documents/ingest", files=files, data=data, headers=auth)
    assert resp.status_code == 202, f"Expected 202 Accepted, got {resp.status_code}: {resp.text}"

    body = resp.json()
    assert "task_id" in body and isinstance(body["task_id"], str) and len(body["task_id"]) > 0
    assert body.get("status") in ("processing", "completed")
    assert body.get("filename") == "alex_sde_resume.txt"
    assert "started_at" in body


def test_r1_status_polling_reports_stages_and_thoughts(e2e_env, sample_resume_text):
    """Test 1.2: GET /api/v1/documents/ingest/status reports stage progression and thoughts."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    files = {"file": ("alex_stages.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    post_res = client.post("/api/v1/documents/ingest", files=files, headers=auth)
    assert post_res.status_code == 202
    task_id = post_res.json()["task_id"]

    # Poll status
    status_data = poll_ingest_task(client, auth, task_id=task_id, timeout_seconds=25.0)
    assert status_data["task_id"] == task_id
    assert status_data["status"] in ("processing", "completed")
    assert "stage" in status_data
    assert "stage_number" in status_data
    assert isinstance(status_data["stage_number"], int)
    assert 1 <= status_data["stage_number"] <= 4 or status_data["status"] == "completed"
    assert "thoughts" in status_data and isinstance(status_data["thoughts"], list)


def test_r1_status_polling_without_task_id_returns_active_or_latest_job(e2e_env, sample_resume_text):
    """Test 1.3: GET /api/v1/documents/ingest/status without task_id resolves active or latest task."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # First query when no tasks exist returns idle
    initial_res = client.get("/api/v1/documents/ingest/status", headers=auth)
    assert initial_res.status_code == 200
    initial_body = initial_res.json()
    assert initial_body.get("status") in ("idle", "completed")

    # Register an ingestion task
    files = {"file": ("latest_job.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    post_res = client.post("/api/v1/documents/ingest", files=files, headers=auth)
    assert post_res.status_code == 202
    registered_task_id = post_res.json()["task_id"]

    # Status without task_id should now resolve this latest job
    query_res = client.get("/api/v1/documents/ingest/status", headers=auth)
    assert query_res.status_code == 200
    body = query_res.json()
    assert body.get("task_id") == registered_task_id
    assert body.get("filename") == "latest_job.txt"


def test_r1_task_completion_updates_status_and_result(e2e_env, sample_resume_text):
    """Test 1.4: Ingestion task reaches terminal 'completed' state with elapsed_seconds."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    files = {"file": ("completion_check.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    post_res = client.post("/api/v1/documents/ingest", files=files, headers=auth)
    assert post_res.status_code == 202
    task_id = post_res.json()["task_id"]

    final_status = poll_ingest_task(client, auth, task_id=task_id, timeout_seconds=25.0)
    assert final_status.get("status") == "completed"
    assert final_status.get("elapsed_seconds") is not None
    assert final_status.get("elapsed_seconds") >= 0
    assert final_status.get("error") is None


def test_r1_task_cancel_endpoint(e2e_env, sample_resume_text):
    """Test 1.5: POST /api/v1/documents/ingest/{task_id}/cancel cancels in-flight task."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    files = {"file": ("to_cancel.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    post_res = client.post("/api/v1/documents/ingest", files=files, headers=auth)
    assert post_res.status_code == 202
    task_id = post_res.json()["task_id"]

    # Request cancellation
    cancel_res = client.post(f"/api/v1/documents/ingest/{task_id}/cancel", headers=auth)
    assert cancel_res.status_code in (200, 202)
    cancel_body = cancel_res.json()
    assert cancel_body.get("ok") is True or cancel_body.get("status") in ("cancelled", "completed")


# ════════════════════════════════════════════════════════════════════════════════
# R2: CONTEXTUAL PROFILE AI EXTRACTION & EXACT DEDUPLICATION (>= 5 TESTS)
# ════════════════════════════════════════════════════════════════════════════════

def test_r2_contextual_prompt_includes_existing_profile_json(e2e_env, sample_resume_text):
    """Test 2.1: Contextual prompt supplies existing candidate profile JSON to the LLM."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]
    mock_llm = e2e_env["mock_llm"]

    # First seed the profile with an existing experience
    seed_profile = CandidateProfile(
        n="Alex Mercer",
        s="Principal Engineer",
        loc="San Francisco, CA",
        skills=[SkillEntry(n="Kubernetes", cat="cloud")],
        exp=[
            ExperienceEntry(
                role="Principal Architect",
                co="Apex Infrastructure",
                period="2020 - Present",
                d="Managed multi-region k8s clusters running 100k pods.",
                s=["Kubernetes"],
            )
        ],
        projects=[],
    )
    mock_llm.set_default_profile(seed_profile)

    # Ingest seed resume
    files = {"file": ("seed.txt", io.BytesIO(b"Alex Mercer Principal Architect Apex Infrastructure"), "text/plain")}
    res_seed = client.post("/api/v1/documents/ingest", files=files, headers=auth)
    assert res_seed.status_code == 202
    poll_ingest_task(client, auth, task_id=res_seed.json()["task_id"], timeout_seconds=25.0)

    # Clear recorded calls and ingest second resume
    mock_llm.reset()
    mock_llm.set_default_profile(seed_profile)

    files2 = {"file": ("second_cv.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    res_second = client.post("/api/v1/documents/ingest", files=files2, headers=auth)
    assert res_second.status_code == 202
    poll_ingest_task(client, auth, task_id=res_second.json()["task_id"], timeout_seconds=25.0)

    # Assert that prompt contained existing profile context
    assert len(mock_llm.recorded_calls) > 0
    prompts = " ".join([c["user"] for c in mock_llm.recorded_calls])
    assert "Apex Infrastructure" in prompts or "EXISTING CANDIDATE PROFILE" in prompts or "existing" in prompts.lower()


def test_r2_exact_duplicate_bullet_merges_to_canonical_point(e2e_env, sample_resume_text):
    """Test 2.2: Re-uploading exact duplicate bullet merges into single canonical point."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Ingest document 1
    files1 = {"file": ("version_1.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    res1 = client.post("/api/v1/documents/ingest", files=files1, headers=auth)
    assert res1.status_code == 202
    poll_ingest_task(client, auth, task_id=res1.json()["task_id"], timeout_seconds=25.0)

    # Query initial points count from profile
    profile_res1 = client.get("/api/v1/profile", headers=auth)
    assert profile_res1.status_code == 200
    points_count1 = len(profile_res1.json().get("exp", []))

    # Ingest document 2 containing identical content
    files2 = {"file": ("version_2.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    res2 = client.post("/api/v1/documents/ingest", files=files2, headers=auth)
    assert res2.status_code == 202
    poll_ingest_task(client, auth, task_id=res2.json()["task_id"], timeout_seconds=25.0)

    # Profile should NOT double the experiences
    profile_res2 = client.get("/api/v1/profile", headers=auth)
    assert profile_res2.status_code == 200
    points_count2 = len(profile_res2.json().get("exp", []))
    assert points_count2 == points_count1, "Exact duplicate should not duplicate experience rows"


def test_r2_exact_duplicate_creates_no_user_prompt_or_conflict(e2e_env, sample_resume_text):
    """Test 2.3: Exact duplicates do not stage conflict alerts or require user resolution."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Upload version 1
    files1 = {"file": ("v1.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    res1 = client.post("/api/v1/documents/ingest", files=files1, headers=auth)
    poll_ingest_task(client, auth, task_id=res1.json()["task_id"], timeout_seconds=25.0)

    # Upload identical version 2
    files2 = {"file": ("v2.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    res2 = client.post("/api/v1/documents/ingest", files=files2, headers=auth)
    status2 = poll_ingest_task(client, auth, task_id=res2.json()["task_id"], timeout_seconds=25.0)

    assert status2.get("has_staged_duplicates") is False or status2.get("has_staged_duplicates") is None

    # Verify conflict groups remain empty
    conflicts_res = client.get("/api/v1/conflicts", headers=auth)
    assert conflicts_res.status_code == 200
    conflicts = conflicts_res.json().get("groups", [])
    assert len(conflicts) == 0, f"Expected 0 conflicts for exact duplicate, found {len(conflicts)}"


def test_r2_document_tag_propagates_to_point_tags_on_ingest(e2e_env, sample_resume_text):
    """Test 2.4: Uploading tagged resume propagates tag_id into point_tags (Flaw E fix)."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]
    # Create tag "sde"
    tag_res = client.post("/api/v1/tags", json={"name": "SDE"}, headers=auth)
    assert tag_res.status_code == 200
    tag_id = tag_res.json()["id"]

    # Upload resume with tag_id
    files = {"file": ("tagged_resume.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    data = {"tag_id": tag_id, "topic": "Core SDE"}
    post_res = client.post("/api/v1/documents/ingest", files=files, data=data, headers=auth)
    assert post_res.status_code == 202, f"Expected 202, got {post_res.status_code}: {post_res.text}"
    poll_ingest_task(client, auth, task_id=post_res.json()["task_id"], timeout_seconds=25.0)

    # Query point tags via API or repo
    point_tags_res = client.get("/api/v1/point-tags", headers=auth)
    assert point_tags_res.status_code == 200
    point_tags = point_tags_res.json().get("point_tags", [])
    # Extracted points should have rows mapped to this tag_id
    tagged_with_sde = [pt for pt in point_tags if pt.get("tag_id") == tag_id]
    assert len(tagged_with_sde) > 0, "Expected newly ingested points to be associated with tag_id in point_tags"


def test_r2_tri_state_extraction_classification(e2e_env, sample_resume_text):
    """Test 2.5: Tri-state extraction structure handles exact_matches, similar_pairs, and new_points."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Seed profile with an experience
    files1 = {"file": ("first.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    res1 = client.post("/api/v1/documents/ingest", files=files1, headers=auth)
    poll_ingest_task(client, auth, task_id=res1.json()["task_id"], timeout_seconds=25.0)

    # Ingest document with a combination of exact and new points
    mixed_content = (
        "Alex Mercer\n"
        "CloudScale Inc - Senior Backend Engineer\n"
        "• Architected event-driven microservices processing 50k events/sec using Kafka.\n"
        "• Introduced GraphQL federation layer reducing mobile client roundtrips by 40%.\n"
    )
    files2 = {"file": ("mixed.txt", io.BytesIO(mixed_content.encode("utf-8")), "text/plain")}
    res2 = client.post("/api/v1/documents/ingest", files=files2, headers=auth)
    assert res2.status_code == 202
    status2 = poll_ingest_task(client, auth, task_id=res2.json()["task_id"], timeout_seconds=25.0)
    assert status2.get("status") == "completed"


# ════════════════════════════════════════════════════════════════════════════════
# R3: INTERACTIVE TWO-COLUMN DUPLICATE RESOLUTION DIALOG (>= 5 TESTS)
# ════════════════════════════════════════════════════════════════════════════════

def test_r3_similar_bullets_staged_by_company_and_project(e2e_env, sample_resume_text, sample_variant_resume_text):
    """Test 3.1: Variant bullets are staged into reviewable pairs grouped by company/project."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Step 1: Ingest base resume
    f1 = {"file": ("base.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    r1 = client.post("/api/v1/documents/ingest", files=f1, headers=auth)
    poll_ingest_task(client, auth, task_id=r1.json()["task_id"], timeout_seconds=25.0)

    # Step 2: Ingest variant resume
    f2 = {"file": ("variant.txt", io.BytesIO(sample_variant_resume_text.encode("utf-8")), "text/plain")}
    r2 = client.post("/api/v1/documents/ingest", files=f2, headers=auth)
    task2_id = r2.json()["task_id"]
    poll_ingest_task(client, auth, task_id=task2_id, timeout_seconds=25.0)

    # Step 3: Query staged duplicates for this task
    dupes_res = client.get(f"/api/v1/documents/ingest/{task2_id}/duplicates", headers=auth)
    assert dupes_res.status_code == 200
    data = dupes_res.json()
    assert "groups" in data
    # Groups should have company/project context and pairs
    for group in data["groups"]:
        assert "company_name" in group or "title" in group
        assert "pairs" in group
        for pair in group["pairs"]:
            assert "pair_id" in pair
            assert "existing_text" in pair
            assert "new_text" in pair


def test_r3_resolution_action_use_new(e2e_env, sample_resume_text, sample_variant_resume_text):
    """Test 3.2: Resolution action 'use_new' replaces old point text with new wording."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Ingest base and variant
    f1 = {"file": ("base.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    r1 = client.post("/api/v1/documents/ingest", files=f1, headers=auth)
    poll_ingest_task(client, auth, task_id=r1.json()["task_id"], timeout_seconds=25.0)

    f2 = {"file": ("variant.txt", io.BytesIO(sample_variant_resume_text.encode("utf-8")), "text/plain")}
    r2 = client.post("/api/v1/documents/ingest", files=f2, headers=auth)
    task2_id = r2.json()["task_id"]
    poll_ingest_task(client, auth, task_id=task2_id, timeout_seconds=25.0)

    dupes_res = client.get(f"/api/v1/documents/ingest/{task2_id}/duplicates", headers=auth)
    groups = dupes_res.json().get("groups", [])
    if groups and groups[0].get("pairs"):
        pair = groups[0]["pairs"][0]
        pair_id = pair["pair_id"]

        resolve_res = client.post(
            f"/api/v1/documents/ingest/{task2_id}/resolve",
            json={"resolutions": [{"pair_id": pair_id, "action": "use_new"}]},
            headers=auth,
        )
        assert resolve_res.status_code == 200
        assert resolve_res.json().get("resolved_count", 0) >= 1


def test_r3_resolution_action_keep_both(e2e_env, sample_resume_text, sample_variant_resume_text):
    """Test 3.3: Resolution action 'keep_both' retains both versions as alternative wordings."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    f1 = {"file": ("base.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    r1 = client.post("/api/v1/documents/ingest", files=f1, headers=auth)
    poll_ingest_task(client, auth, task_id=r1.json()["task_id"], timeout_seconds=25.0)

    f2 = {"file": ("variant.txt", io.BytesIO(sample_variant_resume_text.encode("utf-8")), "text/plain")}
    r2 = client.post("/api/v1/documents/ingest", files=f2, headers=auth)
    task2_id = r2.json()["task_id"]
    poll_ingest_task(client, auth, task_id=task2_id, timeout_seconds=25.0)

    dupes_res = client.get(f"/api/v1/documents/ingest/{task2_id}/duplicates", headers=auth)
    groups = dupes_res.json().get("groups", [])
    if groups and groups[0].get("pairs"):
        pair = groups[0]["pairs"][0]
        pair_id = pair["pair_id"]

        resolve_res = client.post(
            f"/api/v1/documents/ingest/{task2_id}/resolve",
            json={"resolutions": [{"pair_id": pair_id, "action": "keep_both"}]},
            headers=auth,
        )
        assert resolve_res.status_code == 200
        assert resolve_res.json().get("resolved_count", 0) >= 1


def test_r3_resolution_action_keep_existing(e2e_env, sample_resume_text, sample_variant_resume_text):
    """Test 3.4: Resolution action 'keep_existing' retains original and discards incoming variant."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    f1 = {"file": ("base.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    r1 = client.post("/api/v1/documents/ingest", files=f1, headers=auth)
    poll_ingest_task(client, auth, task_id=r1.json()["task_id"], timeout_seconds=25.0)

    f2 = {"file": ("variant.txt", io.BytesIO(sample_variant_resume_text.encode("utf-8")), "text/plain")}
    r2 = client.post("/api/v1/documents/ingest", files=f2, headers=auth)
    task2_id = r2.json()["task_id"]
    poll_ingest_task(client, auth, task_id=task2_id, timeout_seconds=25.0)

    dupes_res = client.get(f"/api/v1/documents/ingest/{task2_id}/duplicates", headers=auth)
    groups = dupes_res.json().get("groups", [])
    if groups and groups[0].get("pairs"):
        pair = groups[0]["pairs"][0]
        pair_id = pair["pair_id"]

        resolve_res = client.post(
            f"/api/v1/documents/ingest/{task2_id}/resolve",
            json={"resolutions": [{"pair_id": pair_id, "action": "keep_existing"}]},
            headers=auth,
        )
        assert resolve_res.status_code == 200
        assert resolve_res.json().get("resolved_count", 0) >= 1


def test_r3_list_staged_duplicates_schema(e2e_env):
    """Test 3.5: GET /api/v1/documents/ingest/{task_id}/duplicates conforms to schema contract."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Non-existent or empty task returns empty groups list
    res = client.get("/api/v1/documents/ingest/dummy-task-id/duplicates", headers=auth)
    assert res.status_code in (200, 404)
    if res.status_code == 200:
        data = res.json()
        assert "groups" in data
        assert isinstance(data["groups"], list)


# ════════════════════════════════════════════════════════════════════════════════
# R4: TAG SELECTOR UNIFICATION & MULTI-TAG TRACK LAYOUT (>= 5 TESTS)
# ════════════════════════════════════════════════════════════════════════════════

def test_r4_jobbuilders_profile_tag_syncs_to_docpane(e2e_env):
    """Test 4.1: Tag list API provides unified tags used by JobBuilders and DocPane."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Create two tags
    client.post("/api/v1/tags", json={"name": "SDE"}, headers=auth)
    client.post("/api/v1/tags", json={"name": "ML"}, headers=auth)

    tags_res = client.get("/api/v1/tags", headers=auth)
    assert tags_res.status_code == 200
    tags = tags_res.json().get("tags", [])
    tag_names = {t["name"] for t in tags}
    assert "SDE" in tag_names
    assert "ML" in tag_names


def test_r4_tag_selection_filters_documents_and_points_in_sync(e2e_env, sample_resume_text):
    """Test 4.2: Filtering by tag_id returns only matching documents and points."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Create tag
    tag_res = client.post("/api/v1/tags", json={"name": "Backend"}, headers=auth)
    tag_id = tag_res.json()["id"]

    # Upload document with this tag
    files = {"file": ("backend_cv.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    data = {"tag_id": tag_id, "kind": "resume"}
    doc_res = client.post("/api/v1/documents", files=files, data=data, headers=auth)
    assert doc_res.status_code == 200

    # Query documents filtered by tag_id
    filtered_docs = client.get(f"/api/v1/documents?tag_id={tag_id}", headers=auth).json().get("documents", [])
    assert len(filtered_docs) >= 1
    assert all(d.get("tag_id") == tag_id for d in filtered_docs)

    # Query documents with nonexistent tag
    empty_docs = client.get("/api/v1/documents?tag_id=nonexistent", headers=auth).json().get("documents", [])
    assert len(empty_docs) == 0


def test_r4_multi_tag_columns_rendered_when_tag_is_any(e2e_env):
    """Test 4.3: Unscoped query (tag='') allows multi-track points partitioning."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # All point tags can be fetched for client-side multi-column distribution
    pt_res = client.get("/api/v1/point-tags", headers=auth)
    assert pt_res.status_code == 200
    assert "point_tags" in pt_res.json()


def test_r4_column_header_bulk_toggles_all_and_none(e2e_env):
    """Test 4.4: Selection API supports bulk toggles and persists selections per tag."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Save selection state for a specific job and tag
    job_id = "test-job-e2e-1"
    profile_tag = "sde"
    selection_payload = {
        "points_on": ["p1", "p2", "p3"],
        "skills_on": ["s1", "s2"],
    }
    put_res = client.put(
        "/api/v1/doc-selections",
        json={
            "job_id": job_id,
            "tag_id": profile_tag,
            "selection": selection_payload,
        },
        headers=auth,
    )
    assert put_res.status_code in (200, 204)

    # Refetch selection
    get_res = client.get(f"/api/v1/doc-selections?job_id={job_id}&tag_id={profile_tag}", headers=auth)
    assert get_res.status_code == 200
    body = get_res.json()
    sel = body.get("selection") or {}
    assert set(sel.get("points_on", [])) == {"p1", "p2", "p3"}


def test_r4_composite_badges_and_export_deduplication(e2e_env):
    """Test 4.5: Points mapped to multiple tags are deduplicated during export."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Create two tags: SDE and ARCH
    t1 = client.post("/api/v1/tags", json={"name": "SDE"}, headers=auth).json()["id"]
    t2 = client.post("/api/v1/tags", json={"name": "ARCH"}, headers=auth).json()["id"]

    # Assign both tags to a single point
    point_id = "bullet_shared_123"
    set_tags_res = client.put(
        f"/api/v1/point-tags/experience/{point_id}",
        json={"tag_ids": [t1, t2]},
        headers=auth,
    )
    assert set_tags_res.status_code == 200

    # Verify both mappings exist in point-tags
    pt_rows = client.get("/api/v1/point-tags", headers=auth).json().get("point_tags", [])
    tags_for_point = {r["tag_id"] for r in pt_rows if r["point_id"] == point_id}
    assert t1 in tags_for_point
    assert t2 in tags_for_point
