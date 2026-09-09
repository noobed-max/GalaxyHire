# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.schema import CandidateProfile, ExperienceEntry, ProjectEntry, SkillEntry
from profile.ingestor import (
    classify_profile_tri_state,
    format_profile_context_for_prompt,
    run,
)
from profile.schema import (
    ContextualCandidateProfile,
    ContextualExperienceExtraction,
    ContextualProfileExtraction,
    ContextualProjectExtraction,
    ExactMatchBullet,
    SimilarBulletPair,
)


def test_format_profile_context_for_prompt():
    profile = {
        "exp": [
            {
                "id": "exp_1",
                "role": "Intern",
                "co": "SuprMentr",
                "period": "2026",
                "points": [{"id": "p_1", "text": "Built distributed backend."}],
            }
        ],
        "projects": [
            {
                "id": "pr_1",
                "title": "Garage S3",
                "points": [{"id": "p_2", "text": "Self-hosted S3."}],
            }
        ],
        "skills": [{"n": "Python"}, {"n": "FastAPI"}],
    }

    ctx = format_profile_context_for_prompt(profile)
    assert "SuprMentr" in ctx
    assert "Garage S3" in ctx
    assert "p_1" in ctx
    assert "Built distributed backend." in ctx
    assert "Python" in ctx


def test_format_profile_context_handles_empty():
    assert format_profile_context_for_prompt(None) == "{}"
    assert format_profile_context_for_prompt({}) == "{}"


def test_tri_state_extraction_schema_valid():
    payload = {
        "name": "Jane Doe",
        "experiences": [
            {
                "role": "Intern",
                "company": "SuprMentr",
                "period": "2026",
                "exact_matches": [{"existing_point_id": "p_1", "text": "Built distributed backend."}],
                "similar_pairs": [
                    {
                        "existing_point_id": "p_2",
                        "existing_text": "Configured block storage.",
                        "new_text": "Configured Longhorn block storage with replication.",
                        "explanation": "Added Longhorn storage replication details.",
                    }
                ],
                "new_points": ["Deployed Prometheus monitoring."],
            }
        ],
        "projects": [
            {
                "title": "Garage S3",
                "stack": ["Rust", "S3"],
                "exact_matches": [],
                "similar_pairs": [],
                "new_points": ["Implemented S3 compatible gateway."],
            }
        ],
    }
    parsed = ContextualProfileExtraction.model_validate(payload)
    assert len(parsed.experiences) == 1
    exp = parsed.experiences[0]
    assert exp.company == "SuprMentr"
    assert exp.exact_matches[0].existing_point_id == "p_1"
    assert exp.similar_pairs[0].new_text.startswith("Configured Longhorn")
    assert exp.new_points[0] == "Deployed Prometheus monitoring."

    assert len(parsed.projects) == 1
    proj = parsed.projects[0]
    assert proj.title == "Garage S3"
    assert proj.new_points[0] == "Implemented S3 compatible gateway."


def test_candidate_profile_backward_compatibility():
    legacy = CandidateProfile(
        n="Alex Developer",
        s="Full-stack engineer",
        skills=[SkillEntry(n="Python", cat="language")],
        exp=[
            ExperienceEntry(
                role="Software Engineer",
                co="TechCorp",
                period="2024-2025",
                d="Maintained core microservices.",
                s=["Python", "Docker"],
            )
        ],
        projects=[
            ProjectEntry(
                title="Search Engine",
                stack=["Go", "Elasticsearch"],
                repo="https://github.com/alex/search",
                impact="Handled 10k QPS at low latency.",
            )
        ],
    )
    assert legacy.n == "Alex Developer"
    assert legacy.exp[0].co == "TechCorp"
    assert legacy.exp[0].exact_matches == []
    assert legacy.exp[0].similar_pairs == []
    assert legacy.exp[0].new_points == []
    assert legacy.projects[0].exact_matches == []


def test_deterministic_tri_state_classifier_exact_and_similar():
    existing_profile = {
        "exp": [
            {
                "id": "exp_suprmentr",
                "role": "Intern",
                "co": "SuprMentr",
                "points": [
                    {"id": "pt_exact", "text": "Built distributed backend on GCP k3s."},
                    {"id": "pt_sim", "text": "Configured Longhorn block storage with automated daily snapshots."},
                ],
            }
        ],
        "projects": [
            {
                "id": "proj_garage",
                "title": "Garage S3",
                "points": [
                    {"id": "pt_proj_exact", "text": "Built distributed object store with geo-replication."},
                ],
            }
        ],
    }

    incoming = CandidateProfile(
        exp=[
            ExperienceEntry(
                role="Intern",
                co="SuprMentr",
                period="2026",
                d=(
                    "- Built distributed backend on GCP k3s.\n"
                    "- Configured Longhorn block storage with automated daily volume snapshots.\n"
                    "- Set up Grafana dashboards for cluster observability."
                ),
            )
        ],
        projects=[
            ProjectEntry(
                title="Garage S3",
                impact=(
                    "- Built distributed object store with geo-replication.\n"
                    "- Added Prometheus metrics endpoint for request tracking."
                ),
            )
        ],
    )

    classified = classify_profile_tri_state(incoming, existing_profile)
    exp = classified.exp[0]

    # 1. Exact match
    assert len(exp.exact_matches) == 1
    assert exp.exact_matches[0].existing_point_id == "pt_exact"

    # 2. Similar pair
    assert len(exp.similar_pairs) == 1
    assert exp.similar_pairs[0].existing_point_id == "pt_sim"
    assert "volume snapshots" in exp.similar_pairs[0].new_text

    # 3. New point
    assert len(exp.new_points) == 1
    assert "Grafana dashboards" in exp.new_points[0]

    # Project checks
    proj = classified.projects[0]
    assert len(proj.exact_matches) == 1
    assert proj.exact_matches[0].existing_point_id == "pt_proj_exact"
    assert len(proj.new_points) == 1
    assert "Prometheus metrics" in proj.new_points[0]


def test_deterministic_tri_state_classifier_fresh_entity():
    existing_profile = {
        "exp": [
            {
                "id": "exp_old",
                "role": "Engineer",
                "co": "OldCorp",
                "points": [{"id": "pt_old", "text": "Old work."}],
            }
        ]
    }
    incoming = CandidateProfile(
        exp=[
            ExperienceEntry(
                role="New Role",
                co="NewCorp",
                d="- First accomplishment.\n- Second accomplishment.",
            )
        ]
    )
    classified = classify_profile_tri_state(incoming, existing_profile)
    assert len(classified.exp[0].exact_matches) == 0
    assert len(classified.exp[0].similar_pairs) == 0
    assert len(classified.exp[0].new_points) == 2


def test_run_offline_contextual_prompting(monkeypatch):
    import llm
    monkeypatch.setattr(llm, "resolve_config", lambda step=None: ("openai", "", "gpt-4o"))
    existing = {
        "exp": [
            {
                "id": "exp_abc",
                "role": "Staff Engineer",
                "co": "AlphaTech",
                "points": [{"id": "pt_alpha_1", "text": "Designed high-throughput Kafka streaming pipeline."}],
            }
        ]
    }
    raw_text = """
    Jane Doe
    Experience
    Staff Engineer | AlphaTech
    2020 - Present
    - Designed high-throughput Kafka streaming pipeline.
    - Reduced end-to-end event latency by 35%.
    """
    result = run(raw=raw_text, existing_profile=existing)
    assert isinstance(result, CandidateProfile)
    # With offline/local heuristic fallback, exact match should be classified
    matching_exps = [e for e in result.exp if "AlphaTech" in e.co or "Staff" in e.role]
    assert len(matching_exps) >= 1
    target = matching_exps[0]
    assert len(target.exact_matches) == 1
    assert target.exact_matches[0].existing_point_id == "pt_alpha_1"
    assert len(target.new_points) == 1
