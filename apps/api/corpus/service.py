"""Corpus-backed discovery — the replacement for JustHireMe's own scraping.

This is where decision D1 becomes code. Two engines, in series, each doing the half it is good at:

    corpus retrieval          ->  this app's RankingService
    (which N of ~4k rows?)        (explain profile fit for the best slice)

Galaxy's retrieval finds candidates over the whole stored corpus with SQL hard filters plus fused
vector/lexical/skill legs. It returns a query-first rank but no explanation. The profile scoring
engine deeply evaluates only the best slice, provides per-criterion detail and Kuzu graph
enrichment, and contributes a deliberately small tie-break.

Neither is redundant: retrieval doesn't explain, and the scoring engine has no way to search a
corpus -- it scores leads someone else found.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from core.logging import get_logger
from core.search_intent import role_title_matches
from corpus.client import CorpusClient, CorpusUnavailable, create_corpus_client

_log = get_logger(__name__)

# How many corpus candidates to score per request.
#
# Re-ranking is the expensive half (deterministic criteria plus a vector search per job), so the
# shortlist is capped independently of how wide retrieval casts its net. Retrieval is cheap and
# benefits from breadth; scoring does not.
#
# Raised from 50 after a user reported "the search results were limited to 50" against a
# 4,931-job corpus. 50 is a defensible *page*, but the UI posts /scan with no body, so this default
# was the entire result set — with no paging and no way to ask for more, it read as the product
# only being able to see 50 jobs.
# Kept for callers that want an explicit recency window (the corpus still supports one);
# the app default is None — unwindowed. Per the 2026-09 ruling freshness is a signal, not a
# filter: every match is served with its posting date shown, fresh rows badge "<24h" and rank
# up via the ranker's recency weight, and nothing is hidden for being old.
FRESH_WINDOW_HOURS = 24

DEFAULT_RERANK_LIMIT = 200

# Deep profile evaluation is for explanations and a small tie-break, not corpus retrieval. Running
# it over all 200 visible jobs both made search take tens of seconds and allowed the résumé-oriented
# score to overwrite the query-first corpus order. The corpus remains the ranking authority; only
# the top slice receives this more expensive second opinion.
DEFAULT_PROFILE_EVAL_LIMIT = 40
PROFILE_TIE_BREAK_WEIGHT = 0.10
PROFILE_EVAL_CONCURRENCY = 8


@dataclass
class CorpusSearchResult:
    """Outcome of one search, including why it might be empty."""

    leads: list[dict[str, Any]] = field(default_factory=list)
    retrieved: int = 0
    reranked: int = 0
    corpus_available: bool = True
    note: str | None = None
    # How many results actually contain a term from the query. Zero means retrieval fell back to
    # semantic nearest-neighbours, which is worth telling the user.
    query_matches: int = 0


def _location_text(location: Any) -> str:
    """Flatten the corpus's structured location into the single string leads carry."""
    if isinstance(location, str):
        return location
    if not isinstance(location, dict):
        return ""
    parts = [location.get("city"), location.get("state"), location.get("country")]
    text = ", ".join(p for p in parts if p)
    if location.get("remote"):
        return f"Remote{f' ({text})' if text else ''}"
    return text


# Words carrying no retrieval signal. Deliberately short: over-stemming a job query throws away
# real terms ("lead", "staff", "go" are all meaningful here).
_STOPWORDS = frozenset({
    "a", "an", "and", "or", "the", "of", "for", "in", "on", "at", "to", "with", "by", "from",
    "as", "is", "are", "be", "role", "roles", "job", "jobs", "position", "positions",
    "engineer", "engineering", "developer", "development", "work", "working", "remote",
})
_MIN_TERM_LEN = 3


def query_terms(query: str | None) -> list[str]:
    """Meaningful lowercase terms from a query, for the keyword-overlap check."""
    if not query:
        return []
    words = [w.strip(".,;:!?()[]{}\"'").lower() for w in query.split()]
    return [w for w in words if len(w) >= _MIN_TERM_LEN and w not in _STOPWORDS]


def count_query_overlap(lead: dict[str, Any], terms: list[str]) -> int:
    """How many query terms appear anywhere in this lead's text."""
    if not terms:
        return 0
    haystack = " ".join(
        [
            str(lead.get("title") or ""),
            str(lead.get("description") or ""),
            " ".join(str(s) for s in (lead.get("tech_stack") or [])),
        ]
    ).lower()
    return sum(1 for t in terms if t in haystack)


def annotate_query_overlap(leads: list[dict[str, Any]], query: str | None) -> tuple[list[dict[str, Any]], int]:
    """Tag each lead with how well it matches the *query*, and report how many matched at all.

    Why this exists: neither score in the pipeline measures query relevance, which was only
    obvious once tested against a real corpus.

      * the corpus's `fit_score` comes out of Reciprocal Rank Fusion, so it is derived from
        *ranks* rather than similarities. Measured here, the nonsense query "underwater basket
        weaving zookeeper" scored **0.500** while "kubernetes platform engineer" scored 0.456 —
        the top hit scores about the same whatever you ask for, so it cannot be a threshold.
      * the re-rank `signal_score` measures fit against the *profile*, not the query. A "Backend
        Developer" posting scores 63 for a platform-engineer profile no matter what was searched.

    The consequence was 50 confidently-scored, completely irrelevant jobs for a gibberish query.
    This does **not** filter them out: semantic retrieval earns its keep precisely when wording
    differs from the query, and dropping non-literal matches would discard that. It annotates, so
    the caller can say "these are the closest matches, not keyword hits" instead of presenting
    noise as results.
    """
    terms = query_terms(query)
    if not terms:
        return leads, len(leads)
    matched = 0
    out = []
    for lead in leads:
        overlap = count_query_overlap(lead, terms)
        if overlap:
            matched += 1
        out.append({**lead, "query_overlap": overlap, "query_terms": len(terms)})
    return out, matched


def coerce_profile_for_scoring(profile: dict[str, Any]) -> dict[str, Any]:
    """Put a profile into the shape `ranking.scoring_engine` reads, if it isn't already.

    Two profile shapes exist in this codebase and they are easy to confuse:

      * the **graph** shape, `{"skills": [{"id", "n", "cat"}]}`, produced by
        `repo.profile.get_profile` — this is what the scoring engine expects
      * the **API/storage** shape, `{"skills": [{"name", "category"}]}`, produced by
        `profile.normalization.normalize_profile_payload`

    `scoring_engine.analyze_candidate` reads `skill.get("n")` with no fallback, so handing it the
    API shape raises `AttributeError` for a plain-string list and silently contributes nothing for
    the `name` variant. Combined with per-lead failure isolation, the visible symptom is *every job
    scoring 0* — which reads as "bad matches" rather than "wrong input", and costs real time to
    diagnose. Cheap to normalize here; expensive to debug later.
    """
    skills = profile.get("skills")
    if not isinstance(skills, list) or not skills:
        return profile
    if any(isinstance(s, dict) and "n" in s for s in skills):
        return profile  # already graph-shaped

    coerced: list[dict[str, Any]] = []
    for s in skills:
        if isinstance(s, str):
            coerced.append({"n": s, "cat": "general"})
        elif isinstance(s, dict):
            name = s.get("name") or s.get("skill") or s.get("n") or ""
            if name:
                coerced.append({"n": str(name), "cat": str(s.get("category") or s.get("cat") or "general")})
    if not coerced:
        return profile
    _log.info("coerced %d profile skills into graph shape for scoring", len(coerced))
    return {**profile, "skills": coerced}


def corpus_job_to_lead(job: dict[str, Any]) -> dict[str, Any]:
    """Map a corpus job onto the lead shape the pipeline, dashboard, and graph already consume.

    Deliberately a straight projection with no scoring: the score is the ranking layer's job, and
    computing it here would put two sources of truth on the same field.
    """
    job_id = job.get("canonical_job_id") or job.get("id") or job.get("primary_url") or ""
    skills = job.get("jd_skills") or job.get("jd_keywords") or []
    raw_fit = next(
        (
            job.get(key)
            for key in ("fit_score", "score", "fit")
            if job.get(key) is not None
        ),
        None,
    )
    try:
        fit = float(raw_fit) if raw_fit is not None else None
    except (TypeError, ValueError):
        fit = None
    # Corpus fit is a 0..1 value. Keep compatibility with an older/custom corpus that may already
    # return a display percentage.
    display_score = (
        max(0, min(100, round(fit * 100 if fit <= 1 else fit)))
        if fit is not None
        else 0
    )

    return {
        "job_id": str(job_id),
        "title": job.get("title") or "",
        "company": job.get("company") or "",
        "url": job.get("primary_url") or job.get("url") or "",
        # `platform` is what the UI shows as the source badge.
        "platform": job.get("site") or job.get("platform") or "corpus",
        "description": job.get("description_md") or job.get("description") or "",
        "kind": "job",
        "location": _location_text(job.get("location")),
        # `score` is the query-first corpus score shown in the pipeline. `signal_score` remains the
        # separate profile evaluation and is added only for the bounded top slice.
        "score": display_score,
        "tech_stack": list(skills)[:20],
        # Provenance the corpus already resolved, kept so the UI can explain where a job came from
        # and the apply flow can prefer the employer's own URL.
        "source_meta": {
            "corpus_job_id": str(job_id),
            "site": job.get("site"),
            "seniority": job.get("seniority"),
            "min_years_experience": job.get("min_years_experience"),
            "onsite_policy": job.get("onsite_policy"),
            "date_posted": job.get("date_posted"),
            # Which 24h claim this result carries ('posted' | 'first_seen' | None) — the
            # UI badges them differently (MAJOR-CHANGE/05 §3). Persisted with the lead,
            # so the badge survives refreshes.
            "freshness": job.get("freshness"),
            "retrieval_score": fit,
            "profile_evaluated": False,
        },
    }


class CorpusDiscoveryService:
    """Retrieve query-first results, then explain and lightly refine the best slice."""

    def __init__(
        self,
        client: CorpusClient | None = None,
        ranking_service: Any | None = None,
        *,
        profile_eval_limit: int = DEFAULT_PROFILE_EVAL_LIMIT,
        profile_tie_break_weight: float = PROFILE_TIE_BREAK_WEIGHT,
    ):
        self._client = client or create_corpus_client()
        self._ranking = ranking_service
        self._profile_eval_limit = max(0, min(50, int(profile_eval_limit)))
        self._profile_tie_break_weight = max(0.0, min(0.25, float(profile_tie_break_weight)))

    def _ranking_service(self):
        # Imported lazily: RankingService pulls in the embedding runtime and the graph, which is a
        # slow import to pay for on app startup when a request may never need it.
        if self._ranking is None:
            from ranking.service import create_ranking_service

            self._ranking = create_ranking_service()
        return self._ranking

    async def search(
        self,
        *,
        query: str | None = None,
        profile: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        skills: list[str] | None = None,
        negative_titles: list[str] | None = None,
        negative_phrases: list[str] | None = None,
        max_years: int | None = None,
        max_seniority: str | None = None,
        remote: bool | None = None,
        location: str | None = None,
        limit: int = DEFAULT_RERANK_LIMIT,
        retrieve_limit: int | None = None,
        rerank: bool = True,
        use_llm: bool = False,
        fresh_hours: int | None = None,
        observed_after: str | None = None,
    ) -> CorpusSearchResult:
        """Search the stored corpus and score the results against the profile.

        `use_llm` defaults to False: the deterministic criteria are the bulk of the signal, and
        paying for an LLM call per job on a 50-result page is rarely worth it. Callers that want
        the richer judgement enable it for a top-K slice.
        """
        try:
            jobs = await self._client.search(
                search_term=query,
                positive_skills=skills,
                negative_titles=negative_titles,
                negative_phrases=negative_phrases,
                max_years=max_years,
                max_seniority=max_seniority,
                remote=remote,
                location=location,
                limit=retrieve_limit or max(limit, DEFAULT_RERANK_LIMIT),
                fresh_hours=fresh_hours,
                observed_after=observed_after,
            )
        except CorpusUnavailable as exc:
            # Not an error the caller should crash on: the profile, pipeline, and graph all work
            # without the corpus, so search degrades to an explained empty result.
            _log.warning("corpus search unavailable: %s", exc)
            return CorpusSearchResult(corpus_available=False, note=str(exc))

        projected = [corpus_job_to_lead(j) for j in jobs]
        # Defence in depth for deployments where the app and corpus are restarted separately. The
        # corpus now applies this known-family gate in SQL, but an older still-running corpus could
        # otherwise continue returning semantic nearest neighbours outside the requested role.
        leads = [lead for lead in projected if role_title_matches(query, lead.get("title"))]
        result = CorpusSearchResult(leads=leads, retrieved=len(leads))

        if not leads:
            if projected:
                result.note = (
                    f"No {query!r} roles passed the title relevance check. "
                    f"{len(projected)} unrelated semantic neighbour"
                    f"{'s were' if len(projected) != 1 else ' was'} rejected."
                )
            else:
                result.note = await self._explain_empty(query)
            return result

        leads, matched = annotate_query_overlap(leads, query)
        result.leads = leads
        result.query_matches = matched
        if query and matched == 0:
            # Retrieval always returns its nearest N, so "nothing matched the words you typed" has
            # to be said out loud or the UI presents noise as results.
            result.note = (
                f"No result contains any term from {query!r}. These are the corpus's closest "
                "semantic matches, not keyword hits — check the spelling or broaden the query."
            )

        if not rerank or not profile:
            return result

        result.leads = await self._rerank(result.leads, profile, settings, use_llm)
        result.reranked = sum(
            bool((lead.get("source_meta") or {}).get("profile_evaluated"))
            for lead in result.leads
        )
        return result

    async def _rerank(
        self,
        leads: list[dict[str, Any]],
        profile: dict[str, Any],
        settings: dict[str, Any] | None,
        use_llm: bool,
    ) -> list[dict[str, Any]]:
        """Deep-evaluate a bounded top slice and use it only as a query-order tie-break.

        A lead whose scoring fails keeps its retrieval position rather than vanishing — losing a
        real job because one criterion threw is worse than showing it unscored. The untouched tail
        remains visible in corpus order, so limiting evaluation never limits search coverage.
        """
        service = self._ranking_service()
        profile = coerce_profile_for_scoring(profile)
        head = leads[: self._profile_eval_limit]
        tail = leads[self._profile_eval_limit :]
        semaphore = asyncio.Semaphore(PROFILE_EVAL_CONCURRENCY)

        async def score(index: int, lead: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            try:
                async with semaphore:
                    verdict = await service.evaluate_lead(lead, profile, settings, use_llm)
            except Exception as exc:  # noqa: BLE001 - one bad lead must not empty the page
                _log.warning("rerank failed for %s: %s", lead.get("job_id"), exc)
                return index, lead
            merged = dict(lead)
            signal_score = int(verdict.get("score") or verdict.get("signal_score") or 0)
            merged["signal_score"] = signal_score
            merged["signal_reason"] = verdict.get("reason") or verdict.get("signal_reason") or ""
            if verdict.get("criteria"):
                merged["score_breakdown"] = verdict["criteria"]
            for key in ("fit_bullets", "signal_tags"):
                if verdict.get(key):
                    merged[key] = verdict[key]
            source_meta = dict(merged.get("source_meta") or {})
            source_meta["profile_evaluated"] = True
            source_meta["profile_tie_break_weight"] = self._profile_tie_break_weight

            # Only blend when the corpus supplied a real fit score. Against an older corpus without
            # that field, retaining retrieval order is safer than silently making the résumé the
            # sole ranking authority again.
            if source_meta.get("retrieval_score") is not None:
                search_score = int(merged.get("score") or 0)
                weight = self._profile_tie_break_weight
                combined = round((1.0 - weight) * search_score + weight * signal_score)
                merged["score"] = max(0, min(100, combined))
                source_meta["combined_rank_score"] = merged["score"]
            merged["source_meta"] = source_meta
            return index, merged

        if not head:
            return leads
        scored = await asyncio.gather(*(score(index, lead) for index, lead in enumerate(head)))

        # Search score is primary. The profile may break a close contest inside the already-best
        # slice, but cannot promote an arbitrary résumé match from the tail over a stronger query
        # result. Original corpus rank is the stable final tie-break.
        evaluated = [
            item
            for _index, item in sorted(
                scored,
                key=lambda pair: (-int(pair[1].get("score") or 0), pair[0]),
            )
        ]
        return [*evaluated, *tail]

    async def _explain_empty(self, query: str | None) -> str:
        """Turn "no results" into something actionable.

        An empty corpus and an unembedded corpus both return zero rows, and the difference decides
        what the user should do: scrape, or wait for embedding to finish.
        """
        try:
            stats = await self._client.stats()
        except CorpusUnavailable:
            return "No results, and corpus statistics were unavailable."
        total = stats.get("canonical_jobs", 0)
        searchable = stats.get("searchable", 0)
        pending = stats.get("pending_embedding", 0)
        if total == 0:
            return "The corpus is empty — run a broad scrape first (services/scraper-node)."
        if searchable == 0:
            return f"{total} jobs are stored but none are embedded yet ({pending} pending), so none are searchable."
        if pending:
            return f"No matches for {query!r} among {searchable} searchable jobs ({pending} still embedding)."
        return f"No matches for {query!r} among {searchable} searchable jobs."

    async def parse_query(self, text: str) -> dict[str, Any]:
        try:
            return await self._client.parse_query(text)
        except CorpusUnavailable as exc:
            _log.warning("corpus parse unavailable: %s", exc)
            from core.search_intent import fallback_search_intent

            return fallback_search_intent(text)

    async def stats(self) -> dict[str, Any]:
        try:
            return {"available": True, **(await self._client.stats())}
        except CorpusUnavailable as exc:
            return {"available": False, "error": str(exc)}

    async def record_feedback(self, canonical_job_id: str, signal: str) -> dict[str, Any]:
        """Record what the user thought of a result, and report the new state of their preference.

        Deliberately *not* wrapped in the `available: False` pattern the read paths use. A failed
        read degrades to an empty page, which is survivable; a failed write that reports success
        teaches the user the button works when their signal went nowhere.
        """
        await self._client.record_feedback(canonical_job_id, signal)
        return {"recorded": True, "preference": await self._client.preference()}

    async def locations(self, q: str) -> dict[str, Any]:
        """Type-ahead for the location box. Degrades to empty rather than failing the page."""
        try:
            return {"available": True, "suggestions": await self._client.locations(q)}
        except CorpusUnavailable as exc:
            return {"available": False, "suggestions": [], "error": str(exc)}

    async def scrape_start(
        self,
        phrase: str,
        hours: int = 24,
        location: str = "",
        portals: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            return {
                "available": True,
                **(await self._client.scrape_start(phrase, hours, location, portals)),
            }
        except CorpusUnavailable as exc:
            return {"available": False, "error": str(exc)}

    async def scrape_fresh(
        self, phrase: str, location: str = "", portals: list[str] | None = None
    ) -> bool:
        """False on any error, so a corpus blip re-scrapes rather than silently skipping."""
        try:
            return await self._client.scrape_fresh(phrase, location, portals)
        except CorpusUnavailable:
            return False

    async def scrape_sources(self) -> dict[str, Any]:
        """The portal catalog the UI lists. Unavailable degrades to an empty list rather than
        an error: the search must still work against stored jobs."""
        try:
            return {"available": True, "sources": await self._client.scrape_sources()}
        except CorpusUnavailable as exc:
            return {"available": False, "sources": [], "error": str(exc)}

    async def scrape_status(self) -> dict[str, Any]:
        """Degrades to "idle, unavailable" rather than raising: the progress poller runs every
        couple of seconds, so a corpus blip must not throw an error dialog at the user."""
        try:
            return {"available": True, **(await self._client.scrape_status())}
        except CorpusUnavailable as exc:
            return {"available": False, "running": False, "status": "idle", "error": str(exc)}

    async def scrape_stop(self) -> dict[str, Any]:
        try:
            return {"available": True, **(await self._client.scrape_stop())}
        except CorpusUnavailable as exc:
            return {"available": False, "error": str(exc)}

    async def scrape_clear_history(self) -> dict[str, Any]:
        try:
            return {"available": True, **(await self._client.scrape_clear_history())}
        except CorpusUnavailable as exc:
            return {"available": False, "error": str(exc)}

    async def preference(self) -> dict[str, Any]:
        try:
            return {"available": True, **(await self._client.preference())}
        except CorpusUnavailable as exc:
            return {"available": False, "error": str(exc)}


def create_corpus_discovery_service(
    client: CorpusClient | None = None, ranking_service: Any | None = None
) -> CorpusDiscoveryService:
    return CorpusDiscoveryService(client, ranking_service)
