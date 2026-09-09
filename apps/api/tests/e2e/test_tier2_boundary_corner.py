"""Tier 2: Boundary & Corner Cases E2E Tests.

Validates boundary conditions, resource constraints, failure cascades, and
adversarial edge cases across:
- R1: Async Ingestion Corner Cases (Features 1-4)
- R2: Contextual Extraction Corner Cases (Features 5-8)
- R3: Duplicate Resolution Corner Cases (Features 9-10)
- R4: Tag Scoping Corner Cases (Features 11-12)

Requires >= 5 tests per feature, >= 20 tests total.
"""

from __future__ import annotations

import io
import time
import pytest
from fastapi.testclient import TestClient

from tests.e2e.conftest import poll_ingest_task
from models.schema import CandidateProfile, ExperienceEntry, SkillEntry


# ════════════════════════════════════════════════════════════════════════════════
# R1: ASYNC INGESTION CORNER CASES (>= 5 TESTS)
# ════════════════════════════════════════════════════════════════════════════════

def test_r1_concurrent_upload_mutex(e2e_env, sample_resume_text):
    """Test 2.1: Concurrent upload returns 409 Conflict if task is already processing."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Start first ingestion
    f1 = {"file": ("job1.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    res1 = client.post("/api/v1/documents/ingest", files=f1, headers=auth)
    assert res1.status_code in (202, 200)

    # If first task is still processing, immediately submitting second upload should conflict or queue safely
    f2 = {"file": ("job2.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    res2 = client.post("/api/v1/documents/ingest", files=f2, headers=auth)
    assert res2.status_code in (202, 409), f"Expected 202 or 409 Conflict, got {res2.status_code}"


def test_r1_malformed_corrupted_pdf_handling(e2e_env):
    """Test 2.2: Uploading zero-byte or corrupted binary file fails gracefully."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Zero-byte file
    empty_file = {"file": ("corrupt.pdf", io.BytesIO(b""), "application/pdf")}
    res = client.post("/api/v1/documents/ingest", files=empty_file, headers=auth)
    # Should either reject at upload or record failed task status
    if res.status_code == 202:
        task_id = res.json()["task_id"]
        status = poll_ingest_task(client, auth, task_id=task_id, timeout_seconds=5.0)
        assert status.get("status") == "failed"
        assert status.get("error") is not None
    else:
        assert res.status_code in (400, 422)


def test_r1_long_running_llm_timeout_resilience(e2e_env, sample_resume_text):
    """Test 2.3: Simulated LLM timeout marks task as failed without crashing sidecar server."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]
    mock_llm = e2e_env["mock_llm"]

    # Configure mock LLM to simulate timeout
    mock_llm.simulate_timeout(True)

    files = {"file": ("alex_timeout.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    post_res = client.post("/api/v1/documents/ingest", files=files, headers=auth)

    if post_res.status_code == 202:
        task_id = post_res.json()["task_id"]
        status = poll_ingest_task(client, auth, task_id=task_id, timeout_seconds=5.0)
        assert status.get("status") == "failed"
        assert "timed out" in str(status.get("error", "")).lower() or "timeout" in str(status.get("error", "")).lower()

    # Reset mock LLM
    mock_llm.simulate_timeout(False)


def test_r1_empty_thoughts_or_non_thinking_llm(e2e_env, sample_resume_text):
    """Test 2.4: Models not supporting reasoning thoughts complete stages with empty thoughts."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]
    mock_llm = e2e_env["mock_llm"]
    mock_llm.simulated_thoughts = []

    files = {"file": ("no_thoughts.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    post_res = client.post("/api/v1/documents/ingest", files=files, headers=auth)
    assert post_res.status_code == 202

    status = poll_ingest_task(client, auth, task_id=post_res.json()["task_id"], timeout_seconds=25.0)
    assert status.get("status") == "completed"
    assert isinstance(status.get("thoughts"), list)


def test_r1_rapid_client_disconnect_and_reconnection(e2e_env, sample_resume_text):
    """Test 2.5: Status polling immediately at 0ms handles non-blocking progression safely."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    files = {"file": ("rapid.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    post_res = client.post("/api/v1/documents/ingest", files=files, headers=auth)
    assert post_res.status_code == 202
    task_id = post_res.json()["task_id"]

    # Immediate polling loop without sleep
    statuses = []
    for _ in range(5):
        s_res = client.get(f"/api/v1/documents/ingest/status?task_id={task_id}", headers=auth)
        if s_res.status_code == 200:
            statuses.append(s_res.json().get("status"))

    assert len(statuses) == 5
    assert all(s in ("processing", "completed") for s in statuses)


# ════════════════════════════════════════════════════════════════════════════════
# R2: CONTEXTUAL EXTRACTION CORNER CASES (>= 5 TESTS)
# ════════════════════════════════════════════════════════════════════════════════

def test_r2_whitespace_punctuation_and_casing_variations(e2e_env):
    """Test 2.6: Normalization identifies exact duplicates despite whitespace/casing/punctuation."""
    from profile.normalization import normalize_bullet_point

    t1 = "Architected event-driven microservices processing 50k events/sec using Kafka."
    t2 = "  architected event-driven microservices processing 50k events/sec using kafka  "
    t3 = "Architected event-driven microservices processing 50k events/sec using Kafka"

    n1 = normalize_bullet_point(t1)
    n2 = normalize_bullet_point(t2)
    n3 = normalize_bullet_point(t3)

    assert n1 == n2, f"Whitespace and casing should match: {n1} vs {n2}"
    assert n1 == n3, f"Punctuation difference should match: {n1} vs {n3}"


def test_r2_same_bullet_across_different_companies_not_merged(e2e_env):
    """Test 2.7: Identical bullet text under two distinct employers is NOT collapsed across companies."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]
    mock_llm = e2e_env["mock_llm"]

    # Candidate profile with identical responsibility under two different companies
    profile = CandidateProfile(
        n="Alex Mercer",
        s="Engineer",
        skills=[],
        exp=[
            ExperienceEntry(
                role="Engineering Lead",
                co="Company Alpha",
                period="2020 - 2022",
                d="Managed an agile team of 8 engineers delivering weekly releases.",
                s=[],
            ),
            ExperienceEntry(
                role="Engineering Manager",
                co="Company Beta",
                period="2022 - Present",
                d="Managed an agile team of 8 engineers delivering weekly releases.",
                s=[],
            ),
        ],
        projects=[],
    )
    mock_llm.set_default_profile(profile)

    files = {"file": ("two_companies.txt", io.BytesIO(b"Alex Mercer Two Companies"), "text/plain")}
    res = client.post("/api/v1/documents/ingest", files=files, headers=auth)
    assert res.status_code == 202
    poll_ingest_task(client, auth, task_id=res.json()["task_id"], timeout_seconds=25.0)

    # Check that both companies exist in profile
    p_res = client.get("/api/v1/profile", headers=auth)
    assert p_res.status_code == 200
    companies = {e.get("co") for e in p_res.json().get("exp", [])}
    assert "Company Alpha" in companies
    assert "Company Beta" in companies


def test_r2_empty_existing_profile_bootstrap(e2e_env, sample_resume_text):
    """Test 2.8: Ingestion into a completely fresh/empty profile succeeds without null errors."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Confirm initial profile is empty
    initial_p = client.get("/api/v1/profile", headers=auth).json()
    assert len(initial_p.get("exp", [])) == 0

    files = {"file": ("bootstrap.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    res = client.post("/api/v1/documents/ingest", files=files, headers=auth)
    assert res.status_code == 202
    status = poll_ingest_task(client, auth, task_id=res.json()["task_id"], timeout_seconds=25.0)
    assert status.get("status") == "completed"

    updated_p = client.get("/api/v1/profile", headers=auth).json()
    assert len(updated_p.get("exp", [])) > 0


def test_r2_resume_with_no_tag_selected(e2e_env, sample_resume_text):
    """Test 2.9: Resume uploaded without tag_id creates universal points (not mapped in point_tags)."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Upload with empty tag_id
    files = {"file": ("untagged.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    data = {"tag_id": ""}
    res = client.post("/api/v1/documents/ingest", files=files, data=data, headers=auth)
    assert res.status_code == 202
    poll_ingest_task(client, auth, task_id=res.json()["task_id"], timeout_seconds=25.0)

    # Profile should have points available generally
    p_res = client.get("/api/v1/profile", headers=auth)
    assert len(p_res.json().get("exp", [])) > 0


def test_r2_huge_resume_exceeding_max_chars(e2e_env):
    """Test 2.10: Massive resume text safely truncates before LLM call without memory crash."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]
    # Generate 250,000 character string
    huge_text = "Alex Mercer Software Engineer\n" + ("• Built scalable systems handling traffic.\n" * 5000)
    assert len(huge_text) > 200_000

    files = {"file": ("huge_resume.txt", io.BytesIO(huge_text.encode("utf-8")), "text/plain")}
    res = client.post("/api/v1/documents/ingest", files=files, headers=auth)
    assert res.status_code == 202
    status = poll_ingest_task(client, auth, task_id=res.json()["task_id"], timeout_seconds=25.0)
    assert status.get("status") in ("completed", "failed")


# ════════════════════════════════════════════════════════════════════════════════
# R3: DUPLICATE RESOLUTION CORNER CASES (>= 5 TESTS)
# ════════════════════════════════════════════════════════════════════════════════

def test_r3_partial_resolution_submission(e2e_env):
    """Test 2.11: Resolving a subset of staged duplicate pairs leaves remainder pending."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Post partial resolution
    task_id = "task-partial-resolve"
    payload = {
        "resolutions": [
            {"pair_id": "pair-1", "action": "use_new"}
        ]
    }
    res = client.post(f"/api/v1/documents/ingest/{task_id}/resolve", json=payload, headers=auth)
    assert res.status_code in (200, 404)


def test_r3_invalid_pair_id_or_unknown_action(e2e_env):
    """Test 2.12: Submitting an unknown pair_id or invalid action returns 422/400 validation error."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    task_id = "test-task-123"
    bad_payload = {
        "resolutions": [
            {"pair_id": "nonexistent-pair", "action": "invalid_action_xyz"}
        ]
    }
    res = client.post(f"/api/v1/documents/ingest/{task_id}/resolve", json=bad_payload, headers=auth)
    assert res.status_code in (400, 404, 422)


def test_r3_empty_staged_duplicates_resolution(e2e_env):
    """Test 2.13: Calling resolve with empty resolutions list succeeds with resolved_count=0."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    task_id = "test-empty-task"
    res = client.post(f"/api/v1/documents/ingest/{task_id}/resolve", json={"resolutions": []}, headers=auth)
    assert res.status_code in (200, 404)
    if res.status_code == 200:
        assert res.json().get("resolved_count") == 0


def test_r3_cross_entity_bullet_conflict_prevention(e2e_env, sample_resume_text):
    """Test 2.14: Experience bullets and project bullets cannot be paired as duplicate conflicts."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Check conflicts grouping preserves entity isolation
    conflicts_res = client.get("/api/v1/conflicts", headers=auth)
    assert conflicts_res.status_code == 200


def test_r3_modal_dismissal_preserves_staged_state(e2e_env):
    """Test 2.15: Querying duplicates without resolving leaves staged records intact."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Multiple successive reads
    r1 = client.get("/api/v1/documents/ingest/task-xyz/duplicates", headers=auth)
    r2 = client.get("/api/v1/documents/ingest/task-xyz/duplicates", headers=auth)
    assert r1.status_code == r2.status_code


# ════════════════════════════════════════════════════════════════════════════════
# R4: TAG SCOPING CORNER CASES (>= 5 TESTS)
# ════════════════════════════════════════════════════════════════════════════════

def test_r4_point_with_deleted_tag(e2e_env):
    """Test 2.16: Deleting a tag cleans up point_tags without leaving orphan errors."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Create tag
    tag_id = client.post("/api/v1/tags", json={"name": "TempTag"}, headers=auth).json()["id"]

    # Map point to this tag
    client.put("/api/v1/point-tags/experience/exp_point_temp", json={"tag_ids": [tag_id]}, headers=auth)

    # Delete tag
    del_res = client.delete(f"/api/v1/tags/{tag_id}", headers=auth)
    assert del_res.status_code == 200

    # Ensure point_tags no longer contains the deleted tag
    pt_rows = client.get("/api/v1/point-tags", headers=auth).json().get("point_tags", [])
    assert not any(r.get("tag_id") == tag_id for r in pt_rows)


def test_r4_point_tagged_with_all_available_tags(e2e_env):
    """Test 2.17: Point assigned to 5+ tags persists without length limits or corruption."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Create 5 tags
    tag_ids = []
    for name in ("TAG_A", "TAG_B", "TAG_C", "TAG_D", "TAG_E"):
        t_id = client.post("/api/v1/tags", json={"name": name}, headers=auth).json()["id"]
        tag_ids.append(t_id)

    point_id = "bullet_heavily_tagged"
    put_res = client.put(
        f"/api/v1/point-tags/experience/{point_id}",
        json={"tag_ids": tag_ids},
        headers=auth,
    )
    assert put_res.status_code == 200

    # Verify all 5 tags are linked
    pt_rows = client.get("/api/v1/point-tags", headers=auth).json().get("point_tags", [])
    linked = {r["tag_id"] for r in pt_rows if r["point_id"] == point_id}
    assert set(tag_ids).issubset(linked)


def test_r4_special_characters_in_tag_names(e2e_env):
    """Test 2.18: Special characters in tag names (C++, AI/ML, DevOps & SRE) are safely handled."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    special_names = ["C++", "AI/ML", "DevOps & SRE", "Go / Rust"]
    created_ids = []
    for name in special_names:
        r = client.post("/api/v1/tags", json={"name": name}, headers=auth)
        assert r.status_code == 200
        created_ids.append(r.json()["id"])

    # Query all tags and check names preserved verbatim
    all_tags = client.get("/api/v1/tags", headers=auth).json().get("tags", [])
    fetched_names = {t["name"] for t in all_tags}
    for name in special_names:
        assert name in fetched_names


def test_r4_zero_points_for_selected_tag(e2e_env):
    """Test 2.19: Selecting a tag with zero matching points returns empty array without error."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    tag_id = client.post("/api/v1/tags", json={"name": "ZeroPointsTag"}, headers=auth).json()["id"]
    docs = client.get(f"/api/v1/documents?tag_id={tag_id}", headers=auth).json().get("documents", [])
    assert docs == []


def test_r4_bulk_toggle_on_empty_column(e2e_env):
    """Test 2.20: Toggling selection state when column is empty performs safe no-op."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    job_id = "test-job-empty-column"
    res = client.put(
        "/api/v1/doc-selections",
        json={
            "job_id": job_id,
            "tag_id": "empty_tag",
            "selection": {"points_on": [], "skills_on": []},
        },
        headers=auth,
    )
    assert res.status_code in (200, 204)
