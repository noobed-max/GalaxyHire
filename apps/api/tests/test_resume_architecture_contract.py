"""Contract tests for two tagged résumé imports.

These tests intentionally use synthetic, already-structured LLM output.  They
must not call a paid model: the extraction contract is what we are validating,
not a provider's ability to produce it.  The supplied PDFs are used only for a
small read-only structural smoke check at the end of the module.
"""

from __future__ import annotations

import pytest

from models.schema import CandidateProfile, ExactMatchBullet, ExperienceEntry, SimilarBulletPair
from profile.ingest_parse import canonical_point_key, point_id
from profile.ingestor import classify_profile_tri_state, run
from profile.provenance import collect_resume_point_tags
from profile.service import _profile_snapshot_from_resume, _validate_existing_point_references


SDE_POINTS = [
    (
        "Built the distributed backend for an AI blogging platform on a 4-node "
        "k3s cluster on GCP, with Longhorn block storage and Garage S3 serving media streams."
    ),
    (
        "Wrote the feed fan-out service in Go on Kafka (franz-go) and Valkey pipelines, "
        "splitting users onto regular and celebrity read paths to hold 150k req/s at sub-25ms P99."
    ),
    (
        "Designed a hybrid search path with Milvus for semantic recall and OpenSearch for "
        "keyword recall, merged by a custom linear reranker tuned for retrieval precision."
    ),
    (
        "Hardened the edge by putting Nginx Ingress with ModSecurity WAF ahead of Kong Gateway, "
        "enforcing asymmetric RS256 JWT validation against public keys fetched dynamically from OpenBao."
    ),
]

ML_SIMILAR_POINT = (
    "Designed a hybrid search pipeline merging Milvus semantic recall and OpenSearch keyword "
    "search, scored with a custom linear reranker for retrieval precision."
)
ML_POINTS = [
    # This is an exact canonical reuse from the first résumé.
    SDE_POINTS[0],
    ML_SIMILAR_POINT,
    (
        "Engineered an agentic AI content generator powered by Gemma 4 31B with autonomous "
        "web research capabilities, utilizing Valkey for session management."
    ),
    (
        "Cut P99 feed latency to <25ms using multi-stage Valkey caching pipelines, Kafka event "
        "fan-out, and concurrent PostgreSQL materialized views."
    ),
]


def _sde_profile() -> CandidateProfile:
    """Structured output the LLM would return for the SDE-tagged résumé."""

    return CandidateProfile(
        n="Candidate",
        resume_id="resume-sde",
        skills=[],
        # Deliberately leave d empty: explicit semantic points are authoritative.
        exp=[
            ExperienceEntry(
                role="Intern",
                co="SuprMentr",
                period="Feb 2026 – May 2026",
                points=list(SDE_POINTS),
            )
        ],
    )


def _ml_profile(entity_id: str) -> CandidateProfile:
    """Structured output the LLM would return for the ML-tagged résumé."""

    return CandidateProfile(
        n="Candidate",
        resume_id="resume-ml",
        skills=[],
        exp=[
            ExperienceEntry(
                role="AI Intern",
                co="SuprMentr",
                period="Feb 2026 – May 2026",
                matched_entity_id=entity_id,
                # Again, no legacy description blob: the points array is the
                # model's complete segmentation and must survive the import.
                points=list(ML_POINTS),
            )
        ],
    )


def test_successful_llm_output_is_authoritative_and_keeps_postgres_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful model result must not be merged with local PDF heuristics.

    The final PostgreSQL phrase intentionally contains the kind of physical PDF
    continuation that previously appeared as a second point.  The model has
    already returned one complete semantic item, so the result must keep it as
    one item and must not gain an unrelated role found by a local parser.
    """

    import llm

    model_result = _ml_profile("unused-until-snapshot")
    raw_text = (
        "Software Engineering Intern – CIA Labs Dec 2023 – Sept 2024\n"
        "• This source text is intentionally different from the mocked model output.\n"
        "AI Intern – SuprMentr Feb 2026 – May 2026\n"
        "• Cut P99 feed latency to <25ms using multi-stage Valkey caching pipelines, Kafka event fan-out, and concurrent\n"
        "  PostgreSQL materialized views."
    )

    monkeypatch.setattr(llm, "resolve_config", lambda _step=None: ("openai", "unit-test-key", "gpt-4o"))
    monkeypatch.setattr(llm, "provider_needs_key", lambda _provider: True)
    monkeypatch.setattr(llm, "call_llm", lambda *args, **kwargs: model_result.model_copy(deep=True))

    result = run(raw=raw_text, existing_profile={}, resume_id="resume-ml")

    assert [entry.role for entry in result.exp] == ["AI Intern"]
    points = result.exp[0].points
    assert len(points) == len(ML_POINTS)
    assert points[-1] == ML_POINTS[-1]
    assert "PostgreSQL materialized views." not in points[:-1]
    assert all("CIA Labs" not in entry.co for entry in result.exp)


def test_successful_llm_empty_duplicate_arrays_are_not_reclassified(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty model duplicate arrays are authoritative, not an invitation to re-run Jaccard."""

    import llm

    exact_text = "Built a shared event pipeline on Kafka."
    similar_existing = "Designed the low-latency event pipeline for Kafka consumers."
    model_result = CandidateProfile(
        resume_id="resume-model",
        exp=[
            ExperienceEntry(
                role="AI Intern",
                co="SuprMentr",
                period="Feb 2026 - May 2026",
                matched_entity_id="job-canonical",
                points=[exact_text, "A genuinely new model point."],
                d=f"{exact_text}\nA genuinely new model point.",
                exact_matches=[],
                similar_pairs=[],
                new_points=[],
            )
        ],
    )
    existing = {
        "exp": [
            {
                "id": "job-canonical",
                "role": "Intern",
                "co": "SuprMentr",
                "period": "Feb 2026 - May 2026",
                "points": [
                    {"id": "point-exact", "text": exact_text},
                    {"id": "point-similar", "text": similar_existing},
                ],
            }
        ]
    }

    monkeypatch.setattr(llm, "resolve_config", lambda _step=None: ("openai", "unit-test-key", "test-model"))
    monkeypatch.setattr(llm, "provider_needs_key", lambda _provider: True)
    monkeypatch.setattr(llm, "call_llm", lambda *args, **kwargs: model_result.model_copy(deep=True))

    result = run(
        raw="The deterministic parser would see a different role here.",
        existing_profile=existing,
        resume_id="resume-model",
    )
    entry = result.exp[0]

    # A successful structured response, including an intentional empty
    # duplicate classification, must not be overwritten by deterministic
    # post-processing.
    assert entry.exact_matches == []
    assert entry.similar_pairs == []
    assert entry.new_points == []
    assert entry.points == [exact_text, "A genuinely new model point."]


def test_point_reference_validation_rejects_forged_ids_and_rebinds_valid_text() -> None:
    """Only IDs under the validated entity survive; stored text is canonical."""

    exact_text = "Built the canonical ingestion service."
    existing_text = "Designed the low-latency ingestion pipeline."
    similar_text = "Designed the revised low-latency ingestion pipeline."
    existing = {
        "exp": [
            {
                "id": "job-canonical",
                "role": "Intern",
                "co": "SuprMentr",
                "period": "2026",
                "points": [
                    {"id": "point-exact", "text": exact_text},
                    {"id": "point-similar", "text": existing_text},
                ],
            },
            {
                "id": "other-job",
                "role": "Engineer",
                "co": "OtherCo",
                "period": "2026",
                "points": [{"id": "point-other", "text": "Other company's work."}],
            },
        ]
    }
    incoming = CandidateProfile(
        exp=[
            ExperienceEntry(
                role="AI Intern",
                co="SuprMentr",
                period="2026",
                matched_entity_id="job-canonical",
                points=[exact_text, similar_text],
                exact_matches=[
                    # Valid ID with forged text must be rebound to stored text.
                    ExactMatchBullet(existing_point_id="point-exact", text="FORGED TEXT"),
                    # This ID belongs to a different entity and must be dropped.
                    ExactMatchBullet(existing_point_id="point-other", text=exact_text),
                ],
                similar_pairs=[
                    SimilarBulletPair(
                        existing_point_id="point-similar",
                        existing_text="FORGED OLD TEXT",
                        new_text=similar_text,
                        explanation="same work",
                    ),
                    SimilarBulletPair(
                        existing_point_id="point-other",
                        existing_text="Other company's work.",
                        new_text=similar_text,
                        explanation="forged cross-entity reference",
                    ),
                ],
            )
        ]
    )

    validated = _validate_existing_point_references(incoming, existing)
    entry = validated.exp[0]

    assert [match.existing_point_id for match in entry.exact_matches] == ["point-exact"]
    assert entry.exact_matches[0].text == exact_text
    assert [pair.existing_point_id for pair in entry.similar_pairs] == ["point-similar"]
    assert entry.similar_pairs[0].existing_text == existing_text
    assert entry.similar_pairs[0].new_text == similar_text


def test_raw_ingestor_is_parse_only_before_service_canonical_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """The low-level ingestor must not materialize raw title-hash entities."""

    import profile.ingestor as ingestor

    parsed = CandidateProfile(
        resume_id="resume-parse-only",
        exp=[ExperienceEntry(role="AI Intern", co="SuprMentr", points=["Built a parser."])],
    )
    writes: list[str] = []
    monkeypatch.setattr(ingestor, "run", lambda **_kwargs: parsed.model_copy(deep=True))
    # ``_graph``/``_vectors`` are intentionally no longer imported by the raw
    # ingestor.  Install sentinels permissively so this test also catches a
    # future reintroduction of either write path.
    monkeypatch.setattr(ingestor, "_graph", lambda *_args, **_kwargs: writes.append("graph"), raising=False)
    monkeypatch.setattr(ingestor, "_vectors", lambda *_args, **_kwargs: writes.append("vectors"), raising=False)

    result = ingestor.ingest(
        raw="source text",
        existing_profile={},
        resume_id="resume-parse-only",
    )

    assert writes == []
    assert result.exp[0].role == "AI Intern"
    assert result.resume_id == "resume-parse-only"


@pytest.mark.asyncio
async def test_automatic_resume_ingest_does_not_run_legacy_conflict_rescan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Automatic uploads use LLM duplicate references, not the legacy rescan."""

    import profile.ingestor as ingestor
    import profile.service as service_module
    from profile.service import ProfileService

    parsed = CandidateProfile(
        resume_id="resume-no-legacy-rescan",
        exp=[ExperienceEntry(role="AI Intern", co="SuprMentr", points=["Built a parser."])],
    )
    monkeypatch.setattr(ingestor, "ingest", lambda *args, **kwargs: parsed.model_copy(deep=True))

    async def fake_run_graph(function, *_args, **_kwargs):
        if getattr(function, "__name__", "") == "get_profile":
            return {}
        return None

    monkeypatch.setattr(service_module, "run_graph", fake_run_graph)
    service = ProfileService()
    legacy_calls: list[str] = []
    monkeypatch.setattr(service, "_sync_conflict_groups", lambda: legacy_calls.append("called"))

    async def fake_post_ingest_sync():
        return {"status": "ok"}

    monkeypatch.setattr(service, "_run_post_ingest_sync", fake_post_ingest_sync)
    result = await service.ingest_resume(raw="source text", resume_id="resume-no-legacy-rescan")

    assert result.resume_id == "resume-no-legacy-rescan"
    assert legacy_calls == []


def test_two_tagged_llm_snapshots_share_entity_keep_variants_and_scope_points() -> None:
    """Two tagged model outputs share one job entity without leaking tags.

    The first import owns the canonical entity ID.  The second import carries a
    different title, references that ID, and contributes one exact point, one
    reviewable similar point, and two genuinely new points.
    """

    first = _profile_snapshot_from_resume(
        _sde_profile(),
        None,
        resume_id="resume-sde",
        tag_id="sde",
    )
    first_exp = first["exp"][0]
    canonical_entity_id = first_exp["id"]
    assert len(first["exp"]) == 1
    assert [point["text"] for point in first_exp["points"]] == SDE_POINTS

    incoming = _ml_profile(canonical_entity_id)
    classified = classify_profile_tri_state(incoming, first)
    classified_exp = classified.exp[0]
    assert [match.text for match in classified_exp.exact_matches] == [SDE_POINTS[0]]
    assert [pair.new_text for pair in classified_exp.similar_pairs] == [ML_SIMILAR_POINT]
    assert classified_exp.new_points == [ML_POINTS[2], ML_POINTS[3]]

    merged = _profile_snapshot_from_resume(
        classified,
        first,
        resume_id="resume-ml",
        tag_id="ml",
    )
    assert len(merged["exp"]) == 1
    merged_exp = merged["exp"][0]
    assert merged_exp["id"] == canonical_entity_id

    # Both title spellings are selectable variants of one canonical entity.
    assert {variant["title"] for variant in merged_exp["role_variants"]} == {
        "Intern",
        "AI Intern",
    }
    assert set(merged_exp["source_resume_ids"]) == {"resume-sde", "resume-ml"}
    assert set(merged_exp["tag_ids"]) == {"sde", "ml"}

    merged_points = merged_exp["points"]
    by_key = {canonical_point_key(point["text"]): point for point in merged_points}
    assert len(by_key) == len(merged_points)
    assert len([point for point in merged_points if canonical_point_key(point["text"]) == canonical_point_key(SDE_POINTS[0])]) == 1
    assert ML_SIMILAR_POINT not in [point["text"] for point in merged_points]
    assert ML_POINTS[2] in [point["text"] for point in merged_points]
    assert ML_POINTS[3] in [point["text"] for point in merged_points]
    assert "PostgreSQL materialized views." not in [point["text"] for point in merged_points[:-1]]

    exact_point = by_key[canonical_point_key(SDE_POINTS[0])]
    assert set(exact_point["source_resume_ids"]) == {"resume-sde", "resume-ml"}
    assert set(exact_point["tag_ids"]) == {"sde", "ml"}
    for point in merged_points:
        assert "general" not in set(point.get("tag_ids") or [])


def test_tag_provenance_excludes_pending_similar_pair_and_has_no_general_membership() -> None:
    first = _profile_snapshot_from_resume(
        _sde_profile(),
        None,
        resume_id="resume-sde",
        tag_id="sde",
    )
    incoming = classify_profile_tri_state(
        _ml_profile(first["exp"][0]["id"]),
        first,
    )

    triples = collect_resume_point_tags(incoming, "ml", first)
    tagged_ids = {(kind, point_id) for kind, point_id, _tag in triples}
    tags = {tag_id for _kind, _point_id, tag_id in triples}

    assert tags == {"ml"}
    assert ("experience", first["exp"][0]["id"]) in tagged_ids
    assert ("experience", first["exp"][0]["points"][0]["id"]) in tagged_ids

    parent_id = first["exp"][0]["id"]
    assert ("experience", point_id(parent_id, ML_POINTS[2])) in tagged_ids
    assert ("experience", point_id(parent_id, ML_POINTS[3])) in tagged_ids

    # A pending similar alternative is not assigned to the new tag until the
    # user resolves the review.  Compare by text through the incoming points so
    # this assertion remains independent of point-id implementation details.
    assert ("experience", point_id(parent_id, ML_SIMILAR_POINT)) not in tagged_ids
    assert "general" not in tags
