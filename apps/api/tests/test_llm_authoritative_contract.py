# SPDX-License-Identifier: AGPL-3.0-only
"""Regression contracts for authoritative LLM profile extraction.

These tests intentionally use a mocked ``call_llm``.  They exercise the
boundary between the model's semantic output and the profile snapshot without
making a paid/provider request.
"""

from __future__ import annotations

from models.schema import (
    CandidateProfile,
    ExactMatchBullet,
    ExperienceEntry,
    SimilarBulletPair,
)
from profile.ingestor import run
from profile.normalization import normalize_candidate_model
from profile.service import _assign_existing_entity_ids, _profile_snapshot_from_resume


def _four_point_resume(*, resume_id: str = "", role: str = "Intern", matched: str = "") -> CandidateProfile:
    points = [
        "Built the distributed backend for an AI blogging platform on a 4-node k3s cluster on GCP, with Longhorn block storage and Garage S3 serving media streams.",
        "Wrote the feed fan-out service in Go on Kafka and Valkey pipelines, splitting users onto regular and celebrity read paths to hold 150k req/s at sub-25ms P99.",
        "Built the hybrid search path with Milvus for semantic recall and OpenSearch for keyword retrieval, merged by a custom linear reranker tuned for retrieval precision.",
        "Hardened the edge with Nginx Ingress and ModSecurity WAF ahead of Kong Gateway, enforcing asymmetric RS256 JWT validation against keys fetched from OpenBao.",
    ]
    return CandidateProfile(
        n="Synthetic Candidate",
        resume_id=resume_id,
        exp=[
            ExperienceEntry(
                role=role,
                co="SuprMentr",
                period="Feb 2026 - May 2026",
                matched_entity_id=matched,
                points=points,
                d="\n".join(points),
            )
        ],
    )


def test_successful_llm_result_is_authoritative_and_does_not_merge_local_parser(monkeypatch):
    """A successful configured model response must be returned as-is semantically."""
    import llm
    import profile.ingestor as ingestor

    model_result = _four_point_resume(resume_id="model-resume", role="AI Intern", matched="job-canonical")
    model_result.exp[0].points = [
        "Cut P99 feed latency to <25ms using multi-stage Valkey caching pipelines, Kafka event fan-out, and concurrent\nPostgreSQL materialized views."
    ]
    model_result.exp[0].d = model_result.exp[0].points[0]
    model_result.exp[0].exact_matches = [
        ExactMatchBullet(existing_point_id="old-point", text="Existing canonical work.")
    ]
    model_result.exp[0].similar_pairs = [
        SimilarBulletPair(
            existing_point_id="similar-point",
            existing_text="Built the old pipeline.",
            new_text="Built the revised pipeline.",
            explanation="Same work with revised wording.",
        )
    ]
    model_result.exp[0].new_points = ["A genuinely new model point."]

    monkeypatch.setattr(llm, "resolve_config", lambda step=None: ("openai", "unit-test-key", "test-model"))
    monkeypatch.setattr(llm, "call_llm", lambda *args, **kwargs: model_result)

    def local_parser_must_not_run(*args, **kwargs):
        raise AssertionError("local parser contaminated a successful LLM extraction")

    monkeypatch.setattr(ingestor, "_parse_local", local_parser_must_not_run)
    result = run(
        raw="Heuristic text that deliberately describes another role and must be ignored.",
        existing_profile={"exp": [{"id": "job-canonical", "role": "Intern", "co": "SuprMentr"}]},
        resume_id="model-resume",
    )

    assert result.resume_id == "model-resume"
    assert [(entry.role, entry.co) for entry in result.exp] == [("AI Intern", "SuprMentr")]
    assert len(result.exp[0].points) == 1
    assert "concurrent PostgreSQL materialized views" in result.exp[0].points[0]
    assert result.exp[0].exact_matches[0].existing_point_id == "old-point"
    assert result.exp[0].similar_pairs[0].existing_point_id == "similar-point"
    assert result.exp[0].new_points == ["A genuinely new model point."]


def test_entity_reference_is_strict_but_unambiguous_intern_title_variant_links():
    existing = {
        "exp": [
            {
                "id": "job-canonical",
                "role": "Intern",
                "co": "SuprMentr",
                "period": "Feb 2026 - May 2026",
            }
        ]
    }

    forged = CandidateProfile(exp=[ExperienceEntry(role="AI Intern", co="SuprMentr", period="Feb 2026 - May 2026", matched_entity_id="not-a-real-id")])
    _assign_existing_entity_ids(forged, existing)
    assert forged.exp[0].matched_entity_id == "job-canonical"

    unrelated = CandidateProfile(exp=[ExperienceEntry(role="AI Intern", co="OtherCo", period="Feb 2026 - May 2026", matched_entity_id="job-canonical")])
    _assign_existing_entity_ids(unrelated, existing)
    assert unrelated.exp[0].matched_entity_id == ""


def test_two_resumes_share_one_job_id_preserve_four_points_each_and_source_metadata():
    first = _four_point_resume(resume_id="resume-a", role="Intern")
    first_snapshot = _profile_snapshot_from_resume(first, {}, resume_id="resume-a", tag_id="sde")
    canonical_job_id = first_snapshot["exp"][0]["id"]

    second_points = [
        "Engineered an agentic AI content generator powered by Gemma with autonomous web research and Valkey session management.",
        "Designed a hybrid search pipeline merging Milvus semantic recall and OpenSearch keyword retrieval with a linear reranker.",
        "Distributed the Hermes AI agent across a GCP k3s cluster, using KEDA to scale workers from Kafka event lag.",
        "Cut P99 feed latency to <25ms using multi-stage Valkey caching pipelines, Kafka event fan-out, and concurrent PostgreSQL materialized views.",
    ]
    second = CandidateProfile(
        n="Synthetic Candidate",
        exp=[
            ExperienceEntry(
                role="AI Intern",
                co="SuprMentr",
                period="Feb 2026 - May 2026",
                matched_entity_id=canonical_job_id,
                points=second_points,
                d="\n".join(second_points),
            )
        ],
    )
    merged = _profile_snapshot_from_resume(second, first_snapshot, resume_id="resume-b", tag_id="ml")

    assert len(merged["exp"]) == 1
    job = merged["exp"][0]
    assert job["id"] == canonical_job_id
    assert {variant["title"] for variant in job["role_variants"]} == {"Intern", "AI Intern"}
    assert set(job["source_resume_ids"]) == {"resume-a", "resume-b"}
    assert set(job["tag_ids"]) == {"sde", "ml"}
    assert len(job["points"]) == 8
    assert {resume_id for point in job["points"] for resume_id in point["source_resume_ids"]} == {"resume-a", "resume-b"}


def test_normalize_candidate_model_preserves_resume_link_fields_and_explicit_point_boundary():
    point = "Cut P99 feed latency to <25ms using multi-stage Valkey caching pipelines, Kafka event fan-out, and concurrent\nPostgreSQL materialized views."
    profile = CandidateProfile(
        resume_id="resume-b",
        exp=[
            ExperienceEntry(
                role="AI Intern",
                co="SuprMentr",
                period="Feb 2026 - May 2026",
                matched_entity_id="job-canonical",
                points=[point],
                d=point,
                exact_matches=[ExactMatchBullet(existing_point_id="p-old", text="old")],
                similar_pairs=[
                    SimilarBulletPair(
                        existing_point_id="p-sim",
                        existing_text="old phrasing",
                        new_text="new phrasing",
                    )
                ],
                new_points=["A new point."],
            )
        ],
    )
    normalized = normalize_candidate_model(profile)

    entry = normalized.exp[0]
    assert normalized.resume_id == "resume-b"
    assert entry.matched_entity_id == "job-canonical"
    assert entry.points == [point]
    assert entry.exact_matches[0].existing_point_id == "p-old"
    assert entry.similar_pairs[0].existing_point_id == "p-sim"
    assert entry.new_points == ["A new point."]
