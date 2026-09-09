"""Search service — planner → retrieval → ranker (docs/04)."""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker
from galaxy.search.planner import compile_query
from galaxy.search.preference import Preference, load_preference
from galaxy.search.profiles import ProfileStore
from galaxy.search.qpp import QualityVerdict, predict
from galaxy.search.ranker import RankedJob, rank
from galaxy.search.retrieval import retrieve


async def search(
    *,
    user_id: UUID | None = None,
    search_term: str | None = None,
    positive_phrases: list[str] | None = None,
    positive_skills: list[str] | None = None,
    negative_titles: list[str] | None = None,
    negative_phrases: list[str] | None = None,
    max_years: int | None = None,
    max_seniority: str | None = None,
    remote: bool | None = None,
    location: str | None = None,
    blacklist: list[str] | None = None,
    limit: int = 50,
    sort: str = "relevance",
    seen_after: str | None = None,
    fresh_hours: int | None = None,
) -> list[RankedJob]:
    profile = await ProfileStore().get(user_id) if user_id else None
    preference = await load_preference(user_id)
    compiled = compile_query(
        search_term=search_term,
        positive_phrases=positive_phrases,
        positive_skills=positive_skills,
        negative_titles=negative_titles,
        negative_phrases=negative_phrases,
        max_years=max_years,
        max_seniority=max_seniority,
        remote=remote,
        location=location,
        blacklist=blacklist,
        profile=profile,
        preference=preference,
        limit=limit,
        seen_after=seen_after,
        fresh_hours=fresh_hours,
    )
    ordered_ids, row_map = await retrieve(compiled)
    return rank(compiled, ordered_ids, row_map, profile, sort=sort)


async def search_with_quality(
    **kwargs,
) -> tuple[list[RankedJob], QualityVerdict, Preference]:
    """`search`, plus a verdict on relevance and the learned preference that was applied.

    Separate entry point rather than a changed return type on `search`: that has many callers and
    tests, and both signals are additive. The verdict is computed from the **cosine similarities**
    the retrieval layer already returns per row, never from the fused rank score — see
    `galaxy/search/qpp.py` for why that distinction decides whether this works at all.

    The Preference is returned rather than merely applied so the caller can *say* what it did. A
    preference layer that quietly removes results is indistinguishable, from the outside, from a
    corpus that simply has less in it.
    """
    sort = kwargs.pop("sort", "relevance")
    user_id = kwargs.get("user_id")
    profile = await ProfileStore().get(user_id) if user_id else None
    preference = await load_preference(user_id)
    compiled = compile_query(
        **{k: v for k, v in kwargs.items() if k != "user_id"},
        profile=profile,
        preference=preference,
    )
    ordered_ids, row_map = await retrieve(compiled)
    ranked = rank(compiled, ordered_ids, row_map, profile, sort=sort)

    # cos_sim comes from `row_map`, which retrieval populates with the raw cosine per candidate.
    # RankedJob deliberately doesn't carry it — it is a retrieval-internal signal, not a result field.
    sims = [
        float(row_map[jid].get("cos_sim") or 0.0)
        for jid in ordered_ids
        if jid in row_map and row_map[jid].get("cos_sim") is not None
    ]
    term_coverage = await corpus_term_coverage(kwargs.get("search_term"))
    from galaxy.common.config import get_settings

    verdict = predict(
        sims,
        term_coverage=term_coverage,
        embedding_version=get_settings().embedding_version,
    )
    return ranked, verdict, preference


async def corpus_term_coverage(search_term: str | None) -> float | None:
    """Fraction of the query's meaningful words that occur anywhere in the corpus.

    The only model-independent signal available, and the one that separates "you searched for
    something this corpus does not contain" from "your wording is unusual but the meaning landed".
    An embedder will happily place an invented word somewhere in vector space; the full-text index
    will not pretend it exists.

    Returns None when there is nothing to measure, which callers treat as "no opinion" rather than
    as zero coverage.
    """
    words = [w.strip(".,;:!?()[]{}\"'").lower() for w in (search_term or "").split()]
    terms = [w for w in words if len(w) > 2]
    if not terms:
        return None

    sm = get_sessionmaker()
    async with sm() as s:
        # `plainto_tsquery` applies the same stemming the index used, so this asks the question the
        # lexical leg would actually ask rather than doing a raw substring match.
        found = 0
        for term in terms:
            hit = (
                await s.execute(
                    text(
                        "SELECT 1 FROM canonical_jobs "
                        "WHERE search_tsv_weighted @@ plainto_tsquery('english', :t) LIMIT 1"
                    ),
                    {"t": term},
                )
            ).first()
            if hit:
                found += 1
    return found / len(terms)


async def get_job(canonical_job_id: str) -> dict | None:
    """Full canonical job + its source observations (docs/07 §3)."""
    sm = get_sessionmaker()
    async with sm() as s:
        job = (
            await s.execute(
                text("SELECT * FROM canonical_jobs WHERE canonical_job_id = :id"),
                {"id": canonical_job_id},
            )
        ).mappings().first()
        if not job:
            return None
        sources = (
            await s.execute(
                text(
                    "SELECT site, source_job_id, url, first_observed_at, last_observed_at "
                    "FROM source_observations WHERE canonical_job_id = :id ORDER BY site"
                ),
                {"id": canonical_job_id},
            )
        ).mappings().all()
    out = dict(job)
    out.pop("embedding", None)  # don't ship the raw vector
    out.pop("search_tsv", None)
    out.pop("search_tsv_weighted", None)
    out["sources"] = [dict(s) for s in sources]
    return out


#: Cap on suggestions returned. Long enough to be useful, short enough to render as a dropdown.
LOCATION_SUGGESTION_LIMIT = 12


async def location_suggestions(q: str, limit: int = LOCATION_SUGGESTION_LIMIT) -> list[dict]:
    """Locations present in the corpus that match `q`, most jobs first.

    Sourced from the stored jobs rather than a hardcoded country list, so a suggestion can never
    return zero results — the user picks from places that demonstrably have jobs. A curated list
    would happily offer "Germany" for a corpus containing none.

    City, state and country are pooled into one list on purpose. The data does not cleanly separate
    them (`country` holds "United States" on some rows and "WA" on others), and the filter matches
    the same three fields, so pooling here keeps the suggestion and the filter consistent. Offering
    a tidier taxonomy than the data supports would mean suggesting things the filter then misses.
    """
    term = (q or "").strip()
    if len(term) < 2:
        return []

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT value AS label, count(*) AS jobs FROM (
                        SELECT location->>'city'    AS value FROM canonical_jobs
                        UNION ALL
                        SELECT location->>'state'   AS value FROM canonical_jobs
                        UNION ALL
                        SELECT location->>'country' AS value FROM canonical_jobs
                    ) pooled
                    WHERE value IS NOT NULL AND value <> '' AND value ILIKE :q
                    GROUP BY value
                    ORDER BY jobs DESC, value ASC
                    LIMIT :lim
                    """
                ),
                {"q": f"%{term}%", "lim": limit},
            )
        ).mappings().all()
    return [{"label": r["label"], "jobs": r["jobs"]} for r in rows]


async def record_feedback(user_id: UUID, canonical_job_id: str, signal: str) -> None:
    """Persist a feedback signal (docs/04 §5). Stage-gated effect is applied elsewhere."""
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO search_feedback (user_id, canonical_job_id, signal) "
                "VALUES (:u, :j, :s)"
            ),
            {"u": str(user_id), "j": canonical_job_id, "s": signal},
        )
        await s.commit()


def _json(v):
    return v if isinstance(v, (dict, list)) else json.loads(v) if v else None
