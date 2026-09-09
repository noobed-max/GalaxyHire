from __future__ import annotations

from core.types import CandidateEvidence, CriterionScore
from ranking.scoring_engine import PostingSignals, _evidence_line, _fmt_terms, clamp



def evaluate_evidence(posting: PostingSignals, candidate: CandidateEvidence, weight: int) -> CriterionScore:
    required = posting.terms
    if not required:
        project_count = len(candidate.project_texts)
        deliverable_overlap = posting.deliverables & candidate.deliverables
        if deliverable_overlap:
            score = min(80, 56 + len(deliverable_overlap) * 12)
        elif project_count:
            score = 50
        else:
            score = 30
        return CriterionScore(
            "Proof of work",
            score,
            weight,
            f"{project_count} profile projects; no exact stack requested",
        )

    values: list[float] = []
    proofed_terms: list[str] = []
    weak_terms: list[str] = []
    for term in required:
        if term in candidate.project_terms:
            values.append(1.0)
            proofed_terms.append(term)
        elif term in candidate.experience_terms:
            values.append(0.75)
            proofed_terms.append(term)
        elif term in candidate.skills:
            values.append(0.45)
            weak_terms.append(term)
        else:
            values.append(0.0)

    term_score = (sum(values) / max(1, len(required))) * 100
    deliverable_overlap = posting.deliverables & candidate.deliverables
    deliverable_score = min(100, 55 + len(deliverable_overlap) * 15) if deliverable_overlap else 45
    score = clamp(term_score * 0.78 + deliverable_score * 0.22)
    if proofed_terms:
        evidence = _evidence_line(candidate, set(proofed_terms))
        reason = "project/experience proof for " + _fmt_terms(proofed_terms)
        if evidence:
            reason += f" ({evidence})"
    elif weak_terms:
        reason = "listed skills only for " + _fmt_terms(weak_terms)
    else:
        reason = "no direct proof for requested stack"
    if deliverable_overlap:
        reason += "; similar deliverable: " + ", ".join(sorted(deliverable_overlap)[:3])
    return CriterionScore("Proof of work", score, weight, reason)
