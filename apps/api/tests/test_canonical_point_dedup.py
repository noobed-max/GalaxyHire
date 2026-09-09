# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest

from profile.ingest_parse import (
    canonical_experience_key,
    canonical_project_key,
    canonical_point_key,
    merge_canonical_points,
    point_id,
    points_with_ids,
    split_points,
)
from models.schema import CandidateProfile, ExperienceEntry, ProjectEntry
from profile.ingestor import classify_profile_tri_state
from profile.normalization import normalize_experiences, normalize_projects
from profile.service import _merge_profile_snapshots
from data.graph.profile_read import merge_profiles


def test_canonical_point_key_normalization():
    variants = [
        "- Built distributed backend on GCP k3s.",
        "• Built distributed backend on GCP k3s",
        "  * **Built distributed backend** on GCP k3s;  ",
        "1. `Built` _distributed_ backend on GCP k3s!",
    ]
    keys = [canonical_point_key(v) for v in variants]
    assert len(set(keys)) == 1
    assert keys[0] == "builtdistributedbackendongcpk3s"


def test_entity_identity_ignores_formatting_dates_and_legal_suffixes_but_keeps_roles_distinct():
    assert canonical_experience_key("Software-Engineer", "Acme, Inc. (2021-25)") == canonical_experience_key(
        "Software Engineer", "ACME Corporation"
    )
    assert canonical_experience_key("Staff Engineer", "Acme Corporation") != canonical_experience_key(
        "Software Engineer", "Acme Corporation"
    )
    assert canonical_project_key("Feed-System (Jan 2022 – May 2025)") == canonical_project_key("Feed System")


def test_snapshot_merge_reuses_entity_and_point_ids_for_format_variants():
    existing = {
        "exp": [{
            "id": "entity-canonical",
            "role": "Software Engineer",
            "co": "Acme Corporation",
            "points": [{"id": "bullet-canonical", "text": "Built payment API."}],
            "d": "Built payment API.",
        }],
        "projects": [{
            "id": "project-canonical",
            "title": "Feed System",
            "points": [{"id": "project-bullet", "text": "Shipped stream processor."}],
            "impact": "Shipped stream processor.",
        }],
    }
    incoming = {
        "exp": [{
            "id": "entity-new-spelling",
            "role": "Software-Engineer",
            "co": "Acme, Inc. (2021-25)",
            "d": "Built payment API.\nAdded audit events.",
        }],
        "projects": [{
            "id": "project-new-spelling",
            "title": "Feed-System (Jan 2022 – May 2025)",
            "impact": "Shipped stream processor.\nAdded replay controls.",
        }],
    }
    merged = _merge_profile_snapshots(existing, incoming)
    assert len(merged["exp"]) == 1 and merged["exp"][0]["id"] == "entity-canonical"
    assert len(merged["exp"][0]["points"]) == 2
    assert merged["exp"][0]["points"][0]["id"] == "bullet-canonical"
    assert "Added audit events." in merged["exp"][0]["d"]
    assert len(merged["projects"]) == 1 and merged["projects"][0]["id"] == "project-canonical"
    assert merged["projects"][0]["points"][0]["id"] == "project-bullet"


def test_graph_profile_merge_keeps_distinct_roles_at_one_company():
    merged = merge_profiles(
        {"exp": [{"id": "role-a", "role": "Software Engineer", "co": "Acme Corp", "d": "A"}]},
        {"exp": [{"id": "role-b", "role": "Staff Engineer", "co": "Acme Corporation", "d": "B"}]},
    )
    assert {row["id"] for row in merged["exp"]} == {"role-a", "role-b"}


def test_tri_state_is_entity_scoped_and_stages_short_bullet_variants():
    existing = {
        "exp": [{
            "id": "job-canonical",
            "role": "Software Engineer",
            "co": "Acme Corporation",
            "points": [{"id": "old-bullet", "text": "Built payment API."}],
        }],
        "projects": [{
            "id": "project-canonical",
            "title": "Feed System",
            "points": [{"id": "old-project-bullet", "text": "Shipped stream processor."}],
        }],
    }
    parsed = CandidateProfile(
        exp=[
            ExperienceEntry(
                role="Software-Engineer",
                co="Acme, Inc. (2021-25)",
                d="Built payment API.\nBuilt payment API with Kafka.",
            ),
            ExperienceEntry(role="Different Role", co="Acme Corporation", d="Built payment API with Kafka."),
        ],
        projects=[
            ProjectEntry(
                title="Feed-System (2022)",
                impact="Shipped stream processor.\nShipped stream processor with replay controls.",
            )
        ],
    )
    result = classify_profile_tri_state(parsed, existing)
    exp = result.exp[0]
    assert [match.existing_point_id for match in exp.exact_matches] == ["old-bullet"]
    assert [pair.new_text for pair in exp.similar_pairs] == ["Built payment API with Kafka."]
    assert result.exp[1].new_points == ["Built payment API with Kafka."]
    assert [match.existing_point_id for match in result.projects[0].exact_matches] == ["old-project-bullet"]
    assert [pair.new_text for pair in result.projects[0].similar_pairs] == ["Shipped stream processor with replay controls."]


def test_canonical_point_key_preserves_distinct_metrics():
    key_4_node = canonical_point_key("Built platform on 4-node cluster.")
    key_8_node = canonical_point_key("Built platform on 8-node cluster.")
    assert key_4_node != key_8_node
    assert "4" in key_4_node
    assert "8" in key_8_node


def test_exact_bullet_reuses_canonical_point_id():
    parent_id = "exp_suprmentr_123"
    prior_points = [
        {"id": "canon_p1", "text": "Built distributed backend on GCP k3s."},
        {"id": "canon_p2", "text": "Configured Longhorn block storage with volume snapshots."},
    ]

    # Incoming blob with exact same bullet 1 (modulo formatting/whitespace) and a new bullet 3
    incoming_blob = """
    * Built distributed backend on GCP k3s
    * Implemented Garage S3 media streaming gateway.
    """

    res = points_with_ids(parent_id, incoming_blob, existing=prior_points)
    assert len(res) == 2
    # Bullet 1 MUST reuse canonical ID
    assert res[0]["id"] == "canon_p1"
    # Bullet 3 gets newly generated hash ID
    assert res[1]["id"] != "canon_p2"
    assert res[1]["id"] == point_id(parent_id, "Implemented Garage S3 media streaming gateway.")


def test_merge_canonical_points_preserves_existing_and_adds_new():
    parent_id = "exp_apple_456"
    existing = [
        {"id": "canon_1", "text": "Wrote Swift services for cloud sync."},
        {"id": "canon_2", "text": "Optimized CoreData sqlite queries by 40%."},
    ]
    incoming = [
        "- Wrote Swift services for cloud sync.",  # Exact duplicate
        "- Implemented background push notification pipelines.",  # Fresh new
    ]

    merged_points, assigned_ids = merge_canonical_points(parent_id, existing, incoming)

    # Must preserve both existing points + add the new point = 3 points
    assert len(merged_points) == 3
    assert merged_points[0]["id"] == "canon_1"
    assert merged_points[1]["id"] == "canon_2"
    assert merged_points[2]["id"] == point_id(parent_id, "Implemented background push notification pipelines.")

    # assigned_ids for the incoming points: canon_1 and new_id
    assert len(assigned_ids) == 2
    assert assigned_ids[0] == "canon_1"
    assert assigned_ids[1] == merged_points[2]["id"]


def test_split_points_normalizes_bullet_variants():
    variants = [
        "• Built distributed backend on GCP k3s.",
        "- Built distributed backend on GCP k3s",
        "  * **Built distributed backend** on GCP k3s;  ",
    ]
    for text in variants:
        pts = split_points(text)
        assert len(pts) == 1
        assert "Built distributed backend" in pts[0]


def test_non_destructive_experience_merge_preserves_bullets():
    """Verify that merging experiences does not wipe bullets due to description length."""
    existing_row = {
        "role": "Intern",
        "company": "SuprMentr",
        "period": "2026",
        "description": "Short bullet A.",
    }
    incoming_row = {
        "role": "Intern",
        "company": "SuprMentr",
        "period": "2026",
        "description": "Short bullet B.",
    }
    merged = normalize_experiences([existing_row, incoming_row])
    assert len(merged) == 1
    desc = merged[0]["description"]
    assert "Short bullet A." in desc
    assert "Short bullet B." in desc


def test_non_destructive_project_merge_preserves_impacts():
    """Verify that merging projects preserves distinct impact bullets across variants."""
    proj_a = {
        "title": "Garage S3",
        "stack": "Rust, S3",
        "impact": "Self-hosted object storage.",
    }
    proj_b = {
        "title": "Garage S3",
        "stack": "Rust, Docker",
        "impact": "Serves high-throughput media streams.",
    }
    merged = normalize_projects([proj_a, proj_b])
    assert len(merged) == 1
    impact = merged[0]["impact"]
    assert "Self-hosted object storage." in impact
    assert "Serves high-throughput media streams." in impact
    assert "S3" in merged[0]["stack"]
    assert "Docker" in merged[0]["stack"]


def test_merge_profile_snapshots_does_not_drop_incoming_experiences():
    existing_profile = {
        "exp": [
            {
                "id": "exp_1",
                "role": "Software Engineer",
                "co": "Acme Corp",
                "d": "Led development of payment gateway.",
                "points": [{"id": "p_canon_1", "text": "Led development of payment gateway."}],
            }
        ]
    }
    incoming_profile = {
        "exp": [
            {
                "role": "Software Engineer",
                "co": "Acme Corp",
                "d": "Led development of payment gateway.\nIntegrated Stripe and PayPal webhooks.",
            }
        ]
    }
    merged = _merge_profile_snapshots(existing_profile, incoming_profile)
    assert len(merged["exp"]) == 1
    exp = merged["exp"][0]
    points = exp.get("points") or []
    assert len(points) == 2
    assert points[0]["id"] == "p_canon_1"
    assert "Integrated Stripe and PayPal webhooks." in points[1]["text"]
