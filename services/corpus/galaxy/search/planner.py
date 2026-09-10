"""Query planner (docs/04 §2).

Compiles a search into a structured query with POSITIVE and NEGATIVE clauses, plus the
role-relevant slice of the profile. Deterministic rules handle the common negatives
(years-of-experience thresholds, seniority bands) so they're reliable without an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from galaxy.ingestion.normalize import extract_min_years
from galaxy.models.enums import Seniority
from galaxy.models.profile import Profile, Project, Skill
from galaxy.search.preference import Preference

_SENIORITY_WORDS: dict[str, Seniority] = {
    "intern": Seniority.INTERN, "internship": Seniority.INTERN,
    "junior": Seniority.JUNIOR, "entry": Seniority.JUNIOR, "entry-level": Seniority.JUNIOR,
    "associate": Seniority.JUNIOR, "new grad": Seniority.JUNIOR,
    "mid": Seniority.MID, "mid-level": Seniority.MID,
    "senior": Seniority.SENIOR, "sr": Seniority.SENIOR,
    "staff": Seniority.LEAD, "principal": Seniority.LEAD, "lead": Seniority.LEAD,
    "director": Seniority.EXEC, "vp": Seniority.EXEC, "head": Seniority.EXEC, "chief": Seniority.EXEC,
}


@dataclass
class PositiveClause:
    text: str = ""
    phrases: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    seniority: Seniority | None = None
    remote: bool | None = None
    location: str | None = None
    # A confident title family is a hard eligibility rule. Semantic retrieval may rank within the
    # family, but it may not substitute an unrelated nearest neighbour merely because vectors
    # always have a nearest point.
    role_family: str | None = None
    role_title_patterns: list[str] = field(default_factory=list)


@dataclass
class NegativeClause:
    titles: list[str] = field(default_factory=list)   # exact-ish title/phrase exclusions
    phrases: list[str] = field(default_factory=list)
    min_years_above: int | None = None                # exclude jobs requiring MORE than this
    seniority_above: Seniority | None = None          # exclude jobs more senior than this
    seniority_below: Seniority | None = None          # exclude jobs more junior than this
    remote_only: bool = False
    companies: list[str] = field(default_factory=list)  # blacklist
    job_ids: list[str] = field(default_factory=list)  # specific jobs the user rejected


@dataclass
class ProfileSlice:
    skills: list[Skill] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    skill_names: list[str] = field(default_factory=list)
    experience: list[dict] = field(default_factory=list)


@dataclass
class CompiledQuery:
    positive: PositiveClause
    negative: NegativeClause
    slice: ProfileSlice
    limit: int = 50
    seen_after: str | None = None  # ISO; "only jobs first ingested since" (saved-search deltas)
    #: Product recency window (hours). None = off. When set, retrieval admits only jobs the
    #: source proves were posted inside it, OR undated jobs that merely LANDED inside it —
    #: the two claims are different and the UI must badge them differently (MAJOR-CHANGE/05 §3).
    fresh_hours: int | None = None
    observed_after: str | None = None  # ISO; only jobs observed by the current collection run


def _detect_seniority(text: str) -> Seniority | None:
    low = text.lower()
    for word, sen in _SENIORITY_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", low):
            return sen
    return None


def slice_for_role(profile: Profile, role_terms: list[str]) -> ProfileSlice:
    """Pull the skills/projects tagged for the searched role (docs/04 §1 key idea).

    Matches project.role_tags / skill.tags against the role terms; falls back to the whole
    profile when nothing is tagged (so a fresh profile still ranks).
    """
    terms = {t.lower() for t in role_terms if t}
    if not terms:
        return ProfileSlice(
            profile.skills, profile.projects, [s.name for s in profile.skills], profile.experience
        )

    def tagged(tags: list[str]) -> bool:
        return any(t.lower() in terms for t in tags)

    projects = [p for p in profile.projects if tagged(p.role_tags)] or profile.projects
    skills = [s for s in profile.skills if tagged(s.tags)] or profile.skills
    # experience entries carry a `tags` list (their field/domain) — slice to the matching field so a
    # multi-field person only surfaces the relevant roles, falling back to all history (docs/11).
    experience = [e for e in profile.experience if tagged(e.get("tags") or [])] or profile.experience
    return ProfileSlice(skills, projects, [s.name for s in skills], experience)


def compile_query(
    *,
    search_term: str | None,
    positive_phrases: list[str] | None = None,
    positive_skills: list[str] | None = None,
    negative_titles: list[str] | None = None,
    negative_phrases: list[str] | None = None,
    max_years: int | None = None,
    max_seniority: str | None = None,
    remote: bool | None = None,
    location: str | None = None,
    blacklist: list[str] | None = None,
    profile: Profile | None = None,
    preference: Preference | None = None,
    limit: int = 50,
    seen_after: str | None = None,
    fresh_hours: int | None = None,
    observed_after: str | None = None,
) -> CompiledQuery:
    """Build a CompiledQuery from explicit UI filters (+ optional free-text term)."""
    term = search_term or ""
    from galaxy.search.roles import role_family

    family = role_family(term)
    positive = PositiveClause(
        text=term,
        phrases=positive_phrases or [],
        skills=positive_skills or [],
        seniority=_detect_seniority(term),
        remote=remote,
        location=location,
        role_family=family.id if family else None,
        role_title_patterns=list(family.title_patterns) if family else [],
    )

    # negative clauses: explicit lists + deterministic parse of "no 3+ years" style intent
    neg = NegativeClause(
        titles=negative_titles or [],
        phrases=negative_phrases or [],
        min_years_above=max_years,
        seniority_above=_SENIORITY_WORDS.get((max_seniority or "").lower()),
        remote_only=bool(remote),
        companies=blacklist or [],
    )
    # if a negative phrase encodes a years threshold ("3+ years"), lift it to min_years_above
    if neg.min_years_above is None:
        for phrase in neg.phrases:
            y = extract_min_years(phrase + " experience")
            if y is not None:
                neg.min_years_above = y if neg.min_years_above is None else min(neg.min_years_above, y)

    # merge the persistent profile blacklist with any request blacklist (docs/04 §5)
    if profile and profile.preferences.blacklist:
        merged = {*(c.lower() for c in neg.companies), *(c.lower() for c in profile.preferences.blacklist)}
        neg.companies = sorted(merged)

    # Learned preference, applied last and *never over an explicit request*. Someone who types
    # "senior engineer" is telling us more than three old too-senior clicks are, so an inferred
    # bound only fills a gap the request left empty. Suppression is different: it names exact jobs
    # the user rejected, so nothing in a query can contradict it.
    if preference is not None:
        neg.job_ids = sorted(preference.suppressed_job_ids)
        if neg.seniority_above is None and preference.max_seniority is not None:
            neg.seniority_above = preference.max_seniority
        if neg.seniority_below is None and preference.min_seniority is not None:
            neg.seniority_below = preference.min_seniority

    role_terms = [term, *(positive_skills or [])]
    prof_slice = (
        slice_for_role(profile, role_terms) if profile else ProfileSlice()
    )
    return CompiledQuery(
        positive=positive, negative=neg, slice=prof_slice, limit=limit, seen_after=seen_after,
        fresh_hours=fresh_hours, observed_after=observed_after,
    )
