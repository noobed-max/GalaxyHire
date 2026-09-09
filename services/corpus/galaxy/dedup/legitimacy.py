"""Deterministic, explainable legitimacy signal (docs/03 §5).

Tri-state (legit / uncertain / scam), default uncertain. Computed from facts already known —
never hides jobs, only annotates the card so users avoid scams.
"""

from __future__ import annotations

import re

from galaxy.models.enums import Legitimacy
from galaxy.models.job import LegitimacyVerdict, SourceObservation

_SCAM_PHRASES = re.compile(
    r"\b(wire transfer|western union|processing fee|registration fee|send money|"
    r"gift card|telegram\s*@|whatsapp only|no experience needed.*\$\d{3,}/day)\b",
    re.I,
)


def assess_legitimacy(
    observations: list[SourceObservation],
    description: str | None,
    company: str,
) -> LegitimacyVerdict:
    signals: list[str] = []
    score = 0  # positive = more legit

    distinct_sites = len({o.site for o in observations})
    if distinct_sites >= 2:
        score += 2
        signals.append(f"seen_on_{distinct_sites}_sources")

    desc = description or ""
    if len(desc) >= 400:
        score += 1
        signals.append("substantial_description")
    elif len(desc) < 80:
        score -= 1
        signals.append("thin_description")

    if _SCAM_PHRASES.search(desc):
        score -= 3
        signals.append("scam_phrasing")

    if not company.strip():
        score -= 1
        signals.append("missing_company")

    if score <= -2:
        verdict = Legitimacy.SCAM
    elif score >= 2:
        verdict = Legitimacy.LEGIT
    else:
        verdict = Legitimacy.UNCERTAIN

    return LegitimacyVerdict(verdict=verdict, signals=signals)
