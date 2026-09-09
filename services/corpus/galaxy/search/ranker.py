"""Fit re-ranking + explanation (docs/04 §3.2, §4).

Re-ranks the RRF-fused candidates with an explainable weighted score. Every signal is
normalized to [0,1] before weighting (raw cosine, ts_rank, and coverage fractions are on
incomparable scales). The displayed fit meter is a calibrated band, not the raw score.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from galaxy.ingestion.normalize import canonical_skill
from galaxy.models.enums import Seniority
from galaxy.models.profile import Profile
from galaxy.search.planner import CompiledQuery

# What decides the order of results.
#
# Rebalanced 2026-07-28 on the user's instruction: "do not ever use the users resume as a filter kind
# of thing on jobs", refined to "rank based on the skills mentioned in the resume … but effectively
# this should be lower priority — let's say the level is set to beginner, then that's higher".
#
# The old split had `skill_overlap` 0.30 + `graph_proof` 0.15 = **0.45 of résumé-derived signal
# against 0.30 for the query**, so what the user typed was outweighed by what happened to be on
# their CV. That is why searching "python backend" surfaced Go roles: the profile had Go.
#
# The order of authority is now explicit:
#
#   1. hard filters (level, location, negatives)  — eligibility, applied in SQL, not here
#   2. `semantic` — how well the job matches the *query*
#   3. everything else — tiebreakers
#
# `preference_fit` stays meaningful because it is not the résumé: it comes from stated preferences
# (remote, location, blacklist), which the user set deliberately.
WEIGHTS = {
    "semantic": 0.60,        # the query — primary
    "skill_overlap": 0.12,   # résumé — weak tiebreaker
    "graph_proof": 0.05,     # résumé — weak tiebreaker
    "preference_fit": 0.15,  # stated preferences, not the CV
    "recency": 0.08,
}

#: Résumé-derived weight, asserted by a test so this cannot creep back up unnoticed.
RESUME_WEIGHT_KEYS = ("skill_overlap", "graph_proof")


@dataclass
class FitExplanation:
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    proof_projects: list[dict] = field(default_factory=list)  # {project_id, skill}
    seniority_delta: int = 0
    freshness_days: float | None = None
    legitimacy: str = "uncertain"
    score_breakdown: dict[str, float] = field(default_factory=dict)
    fit_band: str = "weak"


@dataclass
class RankedJob:
    canonical_job_id: str
    title: str
    company: str
    url: str
    location: dict
    compensation: dict | None
    seniority: str | None
    source_count: int
    fit_score: float
    explanation: FitExplanation
    date_posted: str | None = None  # ISO; drives sort="recent"
    #: The posting text. Omitting this was a real defect: 99.3% of the corpus carries a description,
    #: but search never returned one, so every lead saved into the pipeline had `description: ""`
    #: and opening a job showed nothing. Truncated rather than dropped — a page of 200 results with
    #: full descriptions is megabytes, and the detail view refetches the whole job by id anyway.
    description: str = ""
    #: Which scraper/board produced this job (from source_observations.site). Omitting this was a
    #: real defect too: every lead fell back to platform="corpus" downstream, flattening the whole
    #: pipeline into one undifferentiated bucket. min(sites) when several boards carried the job —
    #: deterministic, and the full observation list stays reachable via the detail endpoint.
    site: str | None = None
    #: Which recency claim this row carries: 'posted' = the source's own date proves it is
    #: inside the display window (BADGE_WINDOW_HOURS), so the UI may badge it "<24h". None =
    #: no badge — the card shows the posting date itself instead. Undated rows never badge:
    #: without a source date there is no freshness claim to make.
    freshness: str | None = None


#: How much posting text a search result carries. Enough to judge a job from the list without
#: opening it; short enough that a 200-result page stays in the low hundreds of KB.
DESCRIPTION_EXCERPT_CHARS = 4000


def _excerpt(text: str | None) -> str:
    """Trim a description to a readable length, cutting on a word boundary."""
    body = (text or "").strip()
    if len(body) <= DESCRIPTION_EXCERPT_CHARS:
        return body
    cut = body[:DESCRIPTION_EXCERPT_CHARS]
    # Back off to the last space so the excerpt doesn't end mid-word.
    space = cut.rfind(" ")
    return (cut[:space] if space > DESCRIPTION_EXCERPT_CHARS // 2 else cut).rstrip() + "…"


_BANDS = [(0.72, "strong"), (0.55, "good"), (0.38, "stretch"), (0.0, "weak")]


def _band(score: float) -> str:
    for threshold, name in _BANDS:
        if score >= threshold:
            return name
    return "weak"


#: Display window for the "<24h" badge (hours). Independent of retrieval filtering, which
#: is unwindowed: the badge answers "is this posting fresh", and only the source's own date
#: can prove it, so it is computed against this fixed window on every search.
BADGE_WINDOW_HOURS = 24


def _recency(date_posted, now: datetime) -> float:
    if not date_posted:
        return 0.3  # unknown recency → neutral-low
    days = max(0.0, (now - date_posted).total_seconds() / 86400)
    return math.exp(-days / 30.0)  # ~1 today, ~0.37 at 30 days


def _freshness_claim(fresh_hours: int | None, date_posted, now: datetime) -> str | None:
    """Whether a result row earns the "<24h" badge.

    Retrieval serves every match with its date shown (per the 2026-09 ruling: freshness is
    a signal, not a filter), so this badges only what the source proves: 'posted' means the
    posting date is inside the window. Older dates and undated rows carry no badge; the card
    shows the date itself (or nothing) instead of an invented claim.
    """
    if date_posted is None:
        return None
    if fresh_hours is None:
        return None  # unwindowed search: no window, no badge — the date chip speaks instead
    age_hours = (now - date_posted).total_seconds() / 3600
    return "posted" if age_hours <= fresh_hours else None


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return values
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.5 for _ in values]  # all equal → neutral
    return [(v - lo) / (hi - lo) for v in values]


def _proof_index(profile: Profile | None) -> dict[str, list[str]]:
    """canonical skill -> project ids that evidence it. Built ONCE per request, not per row
    (the previous per-row O(projects × skills) scan grows with the corpus — docs/04 §3.2)."""
    idx: dict[str, list[str]] = {}
    if not profile:
        return idx
    for project in profile.projects:
        for s in project.skills:
            idx.setdefault(canonical_skill(s), []).append(project.id)
    return idx


def _graph_proof(matched_skills: list[str], proof_index: dict[str, list[str]]) -> tuple[float, list[dict]]:
    """Does a real project evidence the matched skills? (docs/04 §3.2, two-hop join.)

    `matched_skills` and the index keys are both canonicalized, so a profile's "React.js" proves a
    posting's "React".
    """
    if not proof_index or not matched_skills:
        return 0.0, []
    proofs: list[dict] = []
    proven: set[str] = set()
    for skill in matched_skills:
        for pid in proof_index.get(skill, []):
            proofs.append({"project_id": pid, "skill": skill})
            proven.add(skill)
    coverage = len(proven) / len(matched_skills)
    return coverage, proofs


def _as_dict(value) -> dict:
    """JSONB columns arrive as dicts, but tolerate a raw JSON string / None defensively."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        import json

        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _location_fit(pref_locations: list[str], row: dict) -> float:
    """1.0 when the job is remote / unknown / in a preferred place; a mild penalty when it's
    onsite somewhere the user didn't list (docs/04 §3.2 preference axis)."""
    loc = _as_dict(row.get("location"))
    if loc.get("remote") is True or row.get("onsite_policy") == "remote":
        return 1.0
    parts = [str(loc.get(k) or "") for k in ("city", "state", "country")]
    hay = " ".join(p for p in parts if p).lower()
    if not hay.strip():
        return 1.0  # unknown location → neutral, never excluded
    for want in pref_locations:
        w = want.strip().lower()
        if w and w in hay:
            return 1.0
    return 0.9


def _preference_fit(
    compiled: CompiledQuery, profile: Profile | None, row: dict
) -> tuple[float, int]:
    """Non-semantic preference alignment in [0,1]: seniority closeness, remote, comp floor,
    location. Every factor is ≤1 so the product stays a clean normalized signal (docs/04 §3.2)."""
    pref = 1.0
    sen_delta = 0
    target_seniority = compiled.positive.seniority
    job_sen = row.get("seniority")
    if target_seniority and job_sen:
        sen_delta = Seniority(job_sen).rank - target_seniority.rank
        pref *= max(0.0, 1.0 - abs(sen_delta) * 0.2)
    if compiled.positive.remote:
        loc = _as_dict(row.get("location"))
        is_remote = row.get("onsite_policy") == "remote" or loc.get("remote") is True
        pref *= 1.0 if is_remote else 0.7
    prefs = profile.preferences if profile else None
    if prefs and prefs.comp_min:
        comp = _as_dict(row.get("compensation"))
        job_max = comp.get("max_amount") or comp.get("min_amount")
        # scale down postings whose top of band is below the user's floor; unknown comp is neutral
        if isinstance(job_max, (int, float)) and job_max > 0 and job_max < prefs.comp_min:
            pref *= max(0.0, job_max / prefs.comp_min)
    if profile and profile.identity.locations:
        pref *= _location_fit(profile.identity.locations, row)
    return pref, sen_delta


def rank(
    compiled: CompiledQuery,
    ordered_ids: list[str],
    row_map: dict[str, dict],
    profile: Profile | None = None,
    now: datetime | None = None,
    sort: str = "relevance",
) -> list[RankedJob]:
    now = now or datetime.now(UTC)
    # query skills canonicalized through the vocab that produced jd_skills (docs/04 §3.1)
    query_skills = {canonical_skill(s) for s in {*compiled.positive.skills, *compiled.slice.skill_names}}
    proof_index = _proof_index(profile)  # built once, reused per row

    rows = [row_map[i] for i in ordered_ids if i in row_map]
    if not rows:
        return []

    # normalize semantic across the candidate set
    sem_norm = _minmax([float(r.get("cos_sim") or 0.0) for r in rows])

    ranked: list[RankedJob] = []
    for r, sem in zip(rows, sem_norm, strict=True):
        # skill overlap is over the curated jd_skills set, NOT the jd_keywords bag (docs/04 §3.1)
        job_skills = {canonical_skill(k) for k in (r.get("jd_skills") or [])}
        matched = sorted(query_skills & job_skills)
        missing = sorted(query_skills - job_skills)
        skill_overlap = len(matched) / len(query_skills) if query_skills else 0.0

        graph, proofs = _graph_proof(matched, proof_index)

        pref, sen_delta = _preference_fit(compiled, profile, r)
        job_sen = r.get("seniority")

        rec = _recency(r.get("date_posted"), now)

        signals = {
            "semantic": sem,
            "skill_overlap": skill_overlap,
            "graph_proof": graph,
            "preference_fit": pref,
            "recency": rec,
        }
        score = sum(WEIGHTS[k] * v for k, v in signals.items())

        # penalties (docs/04 §3.2)
        legit = (r.get("legitimacy") or {}).get("verdict", "uncertain")
        if legit == "scam":
            score *= 0.3
        elif legit == "uncertain":
            score *= 0.95

        freshness_days = None
        if r.get("date_posted"):
            freshness_days = round((now - r["date_posted"]).total_seconds() / 86400, 1)

        explanation = FitExplanation(
            matched_skills=matched,
            missing_skills=missing,
            proof_projects=proofs,
            seniority_delta=sen_delta,
            freshness_days=freshness_days,
            legitimacy=legit,
            score_breakdown={k: round(WEIGHTS[k] * v, 4) for k, v in signals.items()},
            fit_band=_band(score),
        )
        ranked.append(
            RankedJob(
                canonical_job_id=r["canonical_job_id"],
                title=r["title"],
                company=r["company"],
                url=r["primary_url"],
                location=r.get("location") or {},
                compensation=r.get("compensation"),
                seniority=job_sen,
                source_count=r.get("source_count") or 1,
                fit_score=round(score, 4),
                explanation=explanation,
                date_posted=r["date_posted"].isoformat() if r.get("date_posted") else None,
                description=_excerpt(r.get("description_md")),
                site=r.get("site"),
                freshness=_freshness_claim(BADGE_WINDOW_HOURS, r.get("date_posted"), now),
            )
        )

    if sort == "recent":
        # latest-first: newest date_posted wins, undated sink to the bottom, fit breaks ties
        ranked.sort(
            key=lambda j: (j.date_posted or "", j.fit_score),
            reverse=True,
        )
    else:  # relevance (default)
        ranked.sort(key=lambda j: j.fit_score, reverse=True)
    return ranked[: compiled.limit]


def to_dict(job: RankedJob) -> dict:
    d = asdict(job)
    return d
