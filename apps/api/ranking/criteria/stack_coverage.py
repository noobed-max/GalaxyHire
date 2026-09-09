from __future__ import annotations

from core.types import CandidateEvidence, CriterionScore
from ranking.scoring_engine import PostingSignals, _direct_and_adjacent, _fmt_terms, clamp



def evaluate_stack_coverage(posting: PostingSignals, candidate: CandidateEvidence, weight: int) -> CriterionScore:
    required = posting.terms
    if not required:
        score = 55 if posting.role_tags else 25
        return CriterionScore("Stack overlap", score, weight, "no explicit stack to compare")

    direct, adjacent, missing = _direct_and_adjacent(posting, candidate)
    direct_value = len(direct)
    adjacent_value = 0.30 * len(adjacent)
    coverage = (direct_value + adjacent_value) / max(1, len(required))
    score = clamp((coverage * 88) + min(10, len(direct) * 2))
    if direct and not missing:
        score = max(score, 86)
    elif direct:
        score = max(score, 55)
    elif adjacent:
        score = max(min(38, 22 + len(adjacent) * 4), 22)
    reason = (
        f"matched {_fmt_terms(direct)}"
        + (f"; adjacent {_fmt_terms(adjacent)}" if adjacent else "")
        + (f"; missing {_fmt_terms(missing)}" if missing else "")
    )
    return CriterionScore("Stack overlap", score, weight, reason)
