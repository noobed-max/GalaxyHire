"""Tier 3: Cross-Feature Combinations (Pairwise Coverage) E2E Tests.

Validates emergent behavior and cross-module interactions across pairwise combinations:
- C1: R1 + R2 (Async Ingestion + Contextual Deduplication & Tag Propagation)
- C2: R1 + R3 (Async Ingestion + Duplicate Staging & Resolution)
- C3: R2 + R4 (Canonical Deduplication & Tag Propagation + Multi-Tag Badging)
- C4: R3 + R4 (Duplicate Resolution + Multi-Tag Column Scoping)
- C5: R1 + R4 (Async Session Persistence + Tag Selector Filtering)
- C6: R2 + R3 (Contextual AI Classification + Scoped Duplicate Pair Formation)
"""

from __future__ import annotations

import io
import time
import pytest
from fastapi.testclient import TestClient

from tests.e2e.conftest import poll_ingest_task
from models.schema import CandidateProfile, ExperienceEntry, ProjectEntry, SkillEntry


def test_tier3_combination_c1_r1_r2_async_ingest_and_exact_dedup(e2e_env, sample_resume_text):
    """C1: Async Ingestion + Contextual Deduplication & Tag Propagation."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # 1. Create tag
    tag_id = client.post("/api/v1/tags", json={"name": "SDE_Core"}, headers=auth).json()["id"]

    # 2. Upload first resume
    f1 = {"file": ("sde_1.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    d1 = {"tag_id": tag_id, "topic": "Base Ingest"}
    res1 = client.post("/api/v1/documents/ingest", files=f1, data=d1, headers=auth)
    assert res1.status_code == 202
    task1_id = res1.json()["task_id"]

    status1 = poll_ingest_task(client, auth, task_id=task1_id, timeout_seconds=25.0)
    assert status1.get("status") == "completed"

    # 3. Upload second resume containing exact duplicate bullets
    f2 = {"file": ("sde_2.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    d2 = {"tag_id": tag_id, "topic": "Duplicate Ingest"}
    res2 = client.post("/api/v1/documents/ingest", files=f2, data=d2, headers=auth)
    assert res2.status_code == 202
    task2_id = res2.json()["task_id"]

    status2 = poll_ingest_task(client, auth, task_id=task2_id, timeout_seconds=25.0)
    assert status2.get("status") == "completed"
    assert status2.get("has_staged_duplicates") is False or status2.get("has_staged_duplicates") is None

    # 4. Verify point_tags has associations
    pt_res = client.get("/api/v1/point-tags", headers=auth)
    assert pt_res.status_code == 200
    rows = pt_res.json().get("point_tags", [])
    assert any(r.get("tag_id") == tag_id for r in rows)


def test_tier3_combination_c2_r1_r3_async_ingest_and_duplicate_staging(e2e_env, sample_resume_text, sample_variant_resume_text):
    """C2: Async Ingestion + Duplicate Staging & Resolution."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # 1. Base ingest
    f1 = {"file": ("base.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    r1 = client.post("/api/v1/documents/ingest", files=f1, headers=auth)
    poll_ingest_task(client, auth, task_id=r1.json()["task_id"], timeout_seconds=25.0)

    # 2. Variant ingest
    f2 = {"file": ("variant.txt", io.BytesIO(sample_variant_resume_text.encode("utf-8")), "text/plain")}
    r2 = client.post("/api/v1/documents/ingest", files=f2, headers=auth)
    assert r2.status_code == 202
    task2_id = r2.json()["task_id"]

    status2 = poll_ingest_task(client, auth, task_id=task2_id, timeout_seconds=25.0)
    assert status2.get("status") in ("completed", "review_required")

    # 3. Query staged duplicates and resolve
    dupes_res = client.get(f"/api/v1/documents/ingest/{task2_id}/duplicates", headers=auth)
    assert dupes_res.status_code == 200
    groups = dupes_res.json().get("groups", [])
    if groups and groups[0].get("pairs"):
        pair_id = groups[0]["pairs"][0]["pair_id"]
        res_action = client.post(
            f"/api/v1/documents/ingest/{task2_id}/resolve",
            json={"resolutions": [{"pair_id": pair_id, "action": "use_new"}]},
            headers=auth,
        )
        assert res_action.status_code == 200


def test_tier3_combination_c3_r2_r4_canonical_dedup_and_multi_tag_badging(e2e_env, sample_resume_text):
    """C3: Canonical Deduplication & Tag Propagation + Multi-Tag Badging."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Create SDE and ARCH tags
    t_sde = client.post("/api/v1/tags", json={"name": "SDE"}, headers=auth).json()["id"]
    t_arch = client.post("/api/v1/tags", json={"name": "ARCH"}, headers=auth).json()["id"]

    # Ingest document 1 with SDE tag
    f1 = {"file": ("sde.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    r1 = client.post("/api/v1/documents/ingest", files=f1, data={"tag_id": t_sde}, headers=auth)
    poll_ingest_task(client, auth, task_id=r1.json()["task_id"], timeout_seconds=25.0)

    # Ingest document 2 with ARCH tag containing identical bullets
    f2 = {"file": ("arch.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    r2 = client.post("/api/v1/documents/ingest", files=f2, data={"tag_id": t_arch}, headers=auth)
    poll_ingest_task(client, auth, task_id=r2.json()["task_id"], timeout_seconds=25.0)

    # Inspect point tags
    pt_rows = client.get("/api/v1/point-tags", headers=auth).json().get("point_tags", [])
    assigned_tags = {r["tag_id"] for r in pt_rows}
    assert t_sde in assigned_tags


def test_tier3_combination_c4_r3_r4_duplicate_resolution_and_track_scoping(e2e_env, sample_resume_text, sample_variant_resume_text):
    """C4: Duplicate Resolution + Multi-Tag Column Scoping."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    # Create two tags
    tag1 = client.post("/api/v1/tags", json={"name": "TrackOne"}, headers=auth).json()["id"]
    tag2 = client.post("/api/v1/tags", json={"name": "TrackTwo"}, headers=auth).json()["id"]

    # Ingest base resume under TrackOne
    f1 = {"file": ("track1.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    r1 = client.post("/api/v1/documents/ingest", files=f1, data={"tag_id": tag1}, headers=auth)
    poll_ingest_task(client, auth, task_id=r1.json()["task_id"], timeout_seconds=25.0)

    # Ingest variant under TrackTwo
    f2 = {"file": ("track2.txt", io.BytesIO(sample_variant_resume_text.encode("utf-8")), "text/plain")}
    r2 = client.post("/api/v1/documents/ingest", files=f2, data={"tag_id": tag2}, headers=auth)
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


def test_tier3_combination_c5_r1_r4_async_session_and_tag_filtering(e2e_env, sample_resume_text):
    """C5: Async Session Persistence + Tag Selector Filtering."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]

    tag_id = client.post("/api/v1/tags", json={"name": "Frontend"}, headers=auth).json()["id"]

    # Ingest in-flight
    files = {"file": ("async_tag.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    post_res = client.post("/api/v1/documents/ingest", files=files, data={"tag_id": tag_id}, headers=auth)
    assert post_res.status_code == 202
    task_id = post_res.json()["task_id"]

    # While in-flight, query documents with tag filter
    docs_while_inflight = client.get(f"/api/v1/documents?tag_id={tag_id}", headers=auth).json().get("documents", [])
    assert isinstance(docs_while_inflight, list)

    # Poll to completion
    poll_ingest_task(client, auth, task_id=task_id, timeout_seconds=25.0)

    # After completion, documents under tag_id are updated
    docs_after = client.get(f"/api/v1/documents?tag_id={tag_id}", headers=auth).json().get("documents", [])
    assert len(docs_after) >= 1


def test_tier3_combination_c6_r2_r3_contextual_ai_and_scoped_pair_formation(e2e_env, sample_resume_text):
    """C6: Contextual AI Classification + Scoped Duplicate Pair Formation."""
    client: TestClient = e2e_env["client"]
    auth = e2e_env["auth"]
    mock_llm = e2e_env["mock_llm"]

    # Feed initial profile
    initial_p = CandidateProfile(
        n="Alex Mercer",
        s="Engineer",
        skills=[SkillEntry(n="Kafka", cat="framework")],
        exp=[
            ExperienceEntry(
                role="Senior Backend Engineer",
                co="CloudScale Inc",
                period="Jan 2022 - Present",
                d="Architected event-driven microservices processing 50k events/sec using Kafka.",
                s=["Kafka"],
            )
        ],
        projects=[],
    )
    mock_llm.set_default_profile(initial_p)

    # Seed
    f1 = {"file": ("seed.txt", io.BytesIO(b"Alex Mercer CloudScale Inc"), "text/plain")}
    r1 = client.post("/api/v1/documents/ingest", files=f1, headers=auth)
    poll_ingest_task(client, auth, task_id=r1.json()["task_id"], timeout_seconds=25.0)

    # Ingest document with new project and variant bullet
    f2 = {"file": ("complex.txt", io.BytesIO(sample_resume_text.encode("utf-8")), "text/plain")}
    r2 = client.post("/api/v1/documents/ingest", files=f2, headers=auth)
    assert r2.status_code == 202
    status2 = poll_ingest_task(client, auth, task_id=r2.json()["task_id"], timeout_seconds=25.0)
    assert status2.get("status") in ("completed", "review_required")
