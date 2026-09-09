from __future__ import annotations

from core.types import CandidateEvidence, CriterionScore
from ranking.scoring_engine import PostingSignals, clamp



def evaluate_logistics(posting: PostingSignals, candidate: CandidateEvidence) -> CriterionScore:
    score = 78
    reasons: list[str] = []
    if posting.remote:
        score += 8
        reasons.append("remote-friendly")
    if posting.onsite and not posting.remote:
        score -= 14
        reasons.append("onsite-only constraint")
    if posting.location_limited and candidate.location and candidate.location not in {"US", "United States"}:
        score -= 28
        reasons.append(f"location-limited while profile hints {candidate.location}")
    if posting.red_flags:
        score -= min(35, len(posting.red_flags) * 14)
        reasons.append("red flags: " + ", ".join(posting.red_flags[:3]))
    if not posting.quality_features:
        score -= 18
        reasons.append("thin scraped posting")
    elif len(posting.quality_features) >= 3:
        score += 5
        reasons.append("good posting detail")
    return CriterionScore("Constraints and lead quality", clamp(score), 15, "; ".join(reasons) or "no major constraints found")
