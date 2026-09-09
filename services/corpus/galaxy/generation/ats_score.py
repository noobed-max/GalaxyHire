"""Deterministic ATS score (docs/05 §4).

Reweighted from resume-matcher for OUR pipeline: since we assemble from a template, sections are
always present, so `section_completeness` would be a constant — we replace it with
`keyword_in_context` (a job keyword landing inside an experience/project bullet counts more than
one that only appears in the skills list). Job keywords come from the `jd_keywords` derived field,
so this is a pure lookup consistent with what the ranker used.
"""

from __future__ import annotations

import re

from galaxy.generation.models import AtsBreakdown, ResumeDoc

WEIGHTS = {"keyword_match": 0.55, "skills_coverage": 0.25, "keyword_in_context": 0.20}


def _whole_word(keyword: str, text_lower: str) -> bool:
    esc = re.escape(keyword.strip().lower())
    return bool(esc) and re.search(rf"(?<!\w){esc}(?!\w)", text_lower) is not None


def _resume_text(doc: ResumeDoc) -> str:
    parts = [doc.summary, " ".join(doc.skills)]
    for p in doc.projects:
        parts.append(p.title)
        parts.extend(p.bullets)
    for e in doc.experience:
        parts.append(str(e.get("title", "")))
        parts.extend(e.get("bullets", []) or [])
    return " ".join(parts)


def _bullet_text(doc: ResumeDoc) -> str:
    parts: list[str] = []
    for p in doc.projects:
        parts.extend(p.bullets)
    for e in doc.experience:
        parts.extend(e.get("bullets", []) or [])
    return " ".join(parts)


def score(doc: ResumeDoc, job: dict) -> AtsBreakdown:
    keywords = [k.lower() for k in (job.get("jd_keywords") or [])]
    if not keywords:
        return AtsBreakdown(1.0, 1.0, 1.0, 1.0, [])

    full_lower = _resume_text(doc).lower()
    bullets_lower = _bullet_text(doc).lower()
    skills_lower = " ".join(doc.skills).lower()

    present = [k for k in keywords if _whole_word(k, full_lower)]
    missing = [k for k in keywords if k not in present]
    keyword_match = len(present) / len(keywords)

    # skills_coverage: fraction of keywords covered by the explicit skills list
    covered = [k for k in keywords if _whole_word(k, skills_lower)]
    skills_coverage = len(covered) / len(keywords)

    # keyword_in_context: fraction of PRESENT keywords that appear inside a bullet (not just skills)
    in_context = [k for k in present if _whole_word(k, bullets_lower)]
    keyword_in_context = len(in_context) / len(present) if present else 0.0

    overall = (
        WEIGHTS["keyword_match"] * keyword_match
        + WEIGHTS["skills_coverage"] * skills_coverage
        + WEIGHTS["keyword_in_context"] * keyword_in_context
    )
    return AtsBreakdown(
        overall=round(overall, 4),
        keyword_match=round(keyword_match, 4),
        skills_coverage=round(skills_coverage, 4),
        keyword_in_context=round(keyword_in_context, 4),
        missing_keywords=missing[:25],
    )
