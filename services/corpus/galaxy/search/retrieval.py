"""Hybrid retrieval (docs/04 §2.1, §2.2, §3.1).

Hard filters (SQL predicates over derived columns) → three retrieval legs over the survivors →
Reciprocal Rank Fusion. Embeddings are good at meaning and bad at exact tokens, so a field-weighted
lexical leg (title > skills > company > description) and a skill leg sit beside the vector leg;
RRF fuses *ranks*, avoiding the score-normalization trap.

pgvector >= 0.8 iterative scans keep the filtered ANN from silently returning an empty page.
"""

from __future__ import annotations

import re
from datetime import datetime

import structlog
from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker
from galaxy.ingestion.levels import seniority_to_years
from galaxy.ingestion.normalize import canonical_skill
from galaxy.models.enums import Seniority
from galaxy.search.embedder import Embedder, get_embedder, to_pgvector
from galaxy.search.planner import CompiledQuery
from galaxy.search.roles import lexical_query

log = structlog.get_logger(__name__)

RRF_K = 60  # literature default — do not tune (docs/04 §3.1)
LEG_LIMIT = 200
EF_SEARCH = 120


#: Country shorthands expanded before matching, so "UK" meets "London, United Kingdom"
#: as well as "London, UK". Short tokens match by word-boundary regex rather than substring:
#: a bare `%uk%` would match inside unrelated words, while `\yuk\y` only matches the token
#: itself. Mirrors the alias rules in scraper-node's src/audit/relevance.ts — the ingest
#: gate and this clause must agree, or a located scrape stores rows its own search then hides.
_COUNTRY_ALIASES = {
    "uk": ["uk", "gb", "united kingdom", "great britain", "britain", "england", "scotland", "wales"],
    "gb": ["gb", "uk", "united kingdom", "great britain", "britain"],
    "usa": ["usa", "us", "united states", "united states of america"],
    "us": ["us", "usa", "united states", "united states of america"],
    "uae": ["uae", "united arab emirates", "emirates"],
}
#: Reverse lookup: any member (shorthand OR full name) resolves to the whole set, so a typed
#: "United Kingdom" meets a stored "UK" exactly the way a typed "UK" meets "United Kingdom".
#: First membership wins: "uk" belongs to both sets and must resolve to the wider one.
_ALIAS_OF: dict[str, str] = {}
for _canon, _members in _COUNTRY_ALIASES.items():
    for _alias in _members:
        _ALIAS_OF.setdefault(_alias, _canon)


def _location_parts(value: str) -> list[tuple[list[str], str]]:
    """Location components, each as (ILIKE alternates, word-boundary regex).

    The regex carries the raw token plus every expansion, so short shorthands ("uk") match
    exactly themselves while the ILIKE arm covers the long forms by substring. Both arms
    OR together per component in _hard_filters.
    """
    parts: list[tuple[list[str], str]] = []
    for raw in value.split(","):
        token = " ".join(raw.lower().split())
        if not token:
            continue
        if token in {"bengaluru", "bangalore"}:
            alternates = ["bengaluru", "bangalore"]
        elif token in _ALIAS_OF:
            alternates = _COUNTRY_ALIASES[_ALIAS_OF[token]]
        else:
            alternates = [token]
        like = [f"%{a}%" for a in alternates if len(a) >= 3]
        pattern = r"\y(?:" + "|".join(re.escape(a) for a in alternates) + r")\y"
        parts.append((like, pattern))
    return parts


def _hard_filters(compiled: CompiledQuery, version: str) -> tuple[str, dict]:
    """Build the shared WHERE clause + params applied by every leg (docs/04 §2.1).

    SECURITY NOTE: the returned clause string is composed only of the *static* literals below;
    every user/query-derived value goes into `params` as a bound parameter (`:name`). Callers
    interpolate this clause into their SQL (hence the `# noqa: S608`), so it is safe ONLY as long
    as nothing from the query is ever concatenated into the clause text here.
    """
    clauses = ["status = 'open'", "embedding_version = :version"]
    params: dict = {"version": version}
    neg = compiled.negative
    pos = compiled.positive

    # Known role families are an eligibility boundary, not merely a ranking hint. Without this,
    # the semantic leg always fills the page with its nearest rows even when none are in the role.
    if pos.role_title_patterns:
        clauses.append("lower(title) ~ ANY(:role_title_patterns)")
        params["role_title_patterns"] = pos.role_title_patterns

    # Years, with the level as a fallback.
    #
    # Most postings never state years — only 1,898 of 4,225 in a real corpus — so a plain
    # `min_years_experience <= :max_years` check skipped the majority and a senior role sailed
    # through a "2 years" search. When years are absent the job's *level* stands in via its implied
    # band floor (`galaxy/ingestion/levels.py::YEARS_BAND`), which is the signal the market actually
    # publishes. Only when both are unknown does the row pass unfiltered, keeping the existing
    # "unknown is not excluded" stance.
    if neg.min_years_above is not None:
        too_senior = [s.value for s in Seniority if seniority_to_years(s)[0] > neg.min_years_above]
        # Both conditions must hold, which is the SQL form of "effective floor = max(stated, band)".
        # An OR here let a stated figure override the level, and 17 corpus rows titled "Senior" still
        # claim <= 2 years because their descriptions mention a short secondary requirement — so the
        # level has to be able to veto, not merely to fill in when years are missing.
        clauses.append(
            "(seniority IS NULL OR seniority <> ALL(:years_excluded))"
            " AND (min_years_experience IS NULL OR min_years_experience <= :max_years)"
        )
        params["max_years"] = neg.min_years_above
        params["years_excluded"] = too_senior

    # seniority band: exclude anything more senior than the ceiling
    if neg.seniority_above is not None:
        allowed = [s.value for s in Seniority if s.rank <= neg.seniority_above.rank]
        clauses.append("(seniority IS NULL OR seniority = ANY(:sen_allowed))")
        params["sen_allowed"] = allowed

    if pos.remote is True:
        clauses.append("(onsite_policy = 'remote' OR (location->>'remote')::boolean IS TRUE)")

    # Location. This was previously parsed into the query and then never used — asking for jobs in
    # India returned jobs everywhere, silently, which is the same class of no-op `max_seniority`
    # once was.
    #
    # Matched as a case-insensitive substring across city, state and country because the stored data
    # is genuinely inconsistent: `country` holds "United States" on some rows and a bare state code
    # ("WA", "NY") on others, depending on the board. Requiring an exact field match would drop most
    # of a country's jobs.
    #
    # Remote rows must ALSO name the place — a blanket remote bypass filled country searches with
    # remote-anywhere rows (a "REMOTE (EMEA/APAC)" posting is not a UK job). Bare-"Remote" rows
    # with no region are excluded from located searches, exactly like career-ops' own
    # location_filter (non-empty allow-list: match at least one term), and exactly like the
    # ingest gate's bare-remote rule — the two must agree, or the scrape stores rows its own
    # search then hides.
    if pos.location:
        location_text = (
            "concat_ws(', ', location->>'city', location->>'state', location->>'country')"
        )
        component_clauses = []
        for index, (like_alternates, word_pattern) in enumerate(_location_parts(pos.location)):
            key = f"loc_{index}"
            re_key = f"loc_re_{index}"
            component_clauses.append(
                f"({location_text} ILIKE ANY(:{key}) OR {location_text} ~* :{re_key})"
            )
            params[key] = like_alternates
            params[re_key] = word_pattern
        if component_clauses:
            clauses.append("(" + " AND ".join(component_clauses) + ")")

    # seniority floor: exclude anything more junior than the floor
    if neg.seniority_below is not None:
        allowed_up = [s.value for s in Seniority if s.rank >= neg.seniority_below.rank]
        clauses.append("(seniority IS NULL OR seniority = ANY(:sen_allowed_up))")
        params["sen_allowed_up"] = allowed_up

    if neg.companies:
        clauses.append("lower(company) <> ALL(:blacklist)")
        params["blacklist"] = [c.lower() for c in neg.companies]

    # Jobs the user explicitly marked not relevant. Excluded in SQL rather than filtered from the
    # result set so `limit` still returns a full page — dropping them afterwards would silently
    # shrink every page by however many the user had rejected.
    if neg.job_ids:
        clauses.append("canonical_job_id <> ALL(:suppressed_ids)")
        params["suppressed_ids"] = list(neg.job_ids)

    # Titles are screened first and only against the title. Searching the entire JD for "senior"
    # incorrectly rejected junior postings that merely said "work with a senior engineer".
    if neg.titles:
        clauses.append(
            "NOT (to_tsvector('english', coalesce(title, '')) "
            "@@ websearch_to_tsquery('english', :neg_title_q))"
        )
        params["neg_title_q"] = " OR ".join(neg.titles)

    # Non-title exclusions still apply across the indexed posting text.
    if neg.phrases:
        clauses.append(
            "NOT (search_tsv_weighted @@ websearch_to_tsquery('english', :neg_q))"
        )
        params["neg_q"] = " OR ".join(neg.phrases)

    # "only new since last run" (Phase 6): first_seen_at is when WE ingested it — never NULL,
    # so a delta run reliably returns only jobs new to us since the given timestamp.
    if compiled.seen_after:
        clauses.append("first_seen_at >= :seen_after")
        params["seen_after"] = datetime.fromisoformat(compiled.seen_after)  # asyncpg wants a datetime

    # A fresh dashboard collection may update an existing canonical job rather than create a new
    # row. Filter by last_seen_at for that run so retrieval cannot fall back to yesterday's rows.
    if compiled.observed_after:
        clauses.append("last_seen_at >= :observed_after")
        params["observed_after"] = datetime.fromisoformat(compiled.observed_after)

    # Product recency window (R3, MAJOR-CHANGE/05 §3 P2). Applied independently of the run
    # boundary: `observed_after` only proves a row was *seen* in this run, and `first_seen`
    # portals deliberately keep dated-but-old listings, so a 2022 posting re-scraped today
    # satisfies it. Two arms, deliberately asymmetric:
    #   date_posted inside the window           — the SOURCE proves the posting is fresh
    #   no source date + first_seen inside it   — "new to us", the honest fallback for the
    #                                            dateless boards (first_seen recency policy)
    # A job with a KNOWN stale date never passes, even if it just landed: we scraped an old
    # posting, that's not new information. Undated rows can't prove staleness, so novelty of
    # sighting is the strongest freshness claim available for them — and the ranker labels which
    # arm produced each row (`RankedJob.freshness`, from date_posted nullness against this same
    # window) so the UI badges "posted <24h" and "new to us" as the different claims they are.
    if compiled.fresh_hours:
        clauses.append(
            "(date_posted >= now() - make_interval(hours => :fresh_hours)"
            " OR (date_posted IS NULL AND first_seen_at >= now() - make_interval(hours => :fresh_hours)))"
        )
        params["fresh_hours"] = int(compiled.fresh_hours)

    return " AND ".join(clauses), params


def _query_text(compiled: CompiledQuery) -> str:
    pos = compiled.positive
    return " ".join([pos.text, *pos.phrases, *pos.skills]).strip()


async def retrieve(
    compiled: CompiledQuery, embedder: Embedder | None = None
) -> tuple[list[str], dict[str, dict]]:
    """Return (fused ordered job ids, id→row map for the fused candidates)."""
    embedder = embedder or get_embedder()
    where, base_params = _hard_filters(compiled, embedder.version)
    qtext = _query_text(compiled)
    lexical_text = lexical_query(compiled.positive.text)
    if compiled.positive.phrases or compiled.positive.skills:
        lexical_text = " ".join(
            [lexical_text, *compiled.positive.phrases, *compiled.positive.skills]
        ).strip()
    # canonicalize query skills through the same vocab that produced jd_skills, so the profile's
    # "React.js" matches a posting's "React" (docs/04 §3.1).
    # Query skills only — deliberately **not** `compiled.slice.skill_names`.
    #
    # The profile slice used to be unioned in here, which made the résumé decide which jobs were
    # even *eligible*: its skills joined the skill leg, and with an empty query they became the
    # embedding source outright, so search returned "jobs like my CV" rather than "jobs matching
    # what I typed". Someone searching outside their current stack could not find those jobs at all.
    #
    # The résumé still influences *order*, weakly — see WEIGHTS in ranker.py. Eligibility is decided
    # by the query and the user's explicit filters, and by nothing else.
    skills = sorted({canonical_skill(s) for s in compiled.positive.skills})
    # vector leg source: free text if present, else the skills — so a pure skills+filters query
    # (empty free-text) still gets a semantic leg instead of falling to the skill leg alone.
    embed_source = qtext or " ".join(skills)
    qvec = to_pgvector(embedder.embed([embed_source])[0]) if embed_source else None

    sm = get_sessionmaker()
    async with sm() as session:
        # semantic leg needs iterative scans in-transaction (docs/04 §2.2)
        await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        await session.execute(text(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}"))

        legs: list[list[str]] = []

        # 1) lexical
        if lexical_text:
            lex = await session.execute(
                text(
                    f"SELECT canonical_job_id FROM canonical_jobs "  # noqa: S608 (where from static clauses)
                    f"WHERE {where} "
                    f"AND search_tsv_weighted @@ websearch_to_tsquery('english', :q) "
                    f"ORDER BY ts_rank_cd(search_tsv_weighted, "
                    f"websearch_to_tsquery('english', :q)) DESC "
                    f"LIMIT :n"
                ),
                {**base_params, "q": lexical_text, "n": LEG_LIMIT},
            )
            legs.append([r[0] for r in lex.all()])

        # 2) semantic (vector cosine)
        if qvec is not None:
            sem = await session.execute(
                text(
                    f"SELECT canonical_job_id FROM canonical_jobs "  # noqa: S608
                    f"WHERE {where} ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :n"
                ),
                {**base_params, "qvec": qvec, "n": LEG_LIMIT},
            )
            legs.append([r[0] for r in sem.all()])

        # 3) skill overlap — over the curated jd_skills set, NOT the jd_keywords bag (docs/04 §3.1)
        if skills:
            sk = await session.execute(
                text(
                    f"SELECT canonical_job_id, "  # noqa: S608
                    f"(SELECT count(*) FROM jsonb_array_elements_text(jd_skills) k "
                    f"  WHERE lower(k) = ANY(:skills)) AS overlap "
                    f"FROM canonical_jobs WHERE {where} "
                    f"ORDER BY overlap DESC LIMIT :n"
                ),
                {**base_params, "skills": skills, "n": LEG_LIMIT},
            )
            legs.append([r[0] for r in sk.all() if r[1] and r[1] > 0])

        fused = rrf_fuse(legs)
        if not fused:
            return [], {}

        top_ids = fused[: max(compiled.limit * 4, 50)]
        # cosine similarity computed in SQL so the ranker gets `cos_sim` directly
        cos_expr = "1 - (embedding <=> CAST(:qvec AS vector))" if qvec is not None else "0.0"
        # The sighting guard: a canonical row with no source_observations is merge debris,
        # not a job (a non-atomic merge can strand one; the finalize sweep deletes them,
        # but search must never serve one — its site would be NULL and the UI would fall
        # back to platform="corpus").
        rows = await session.execute(
            text(
                f"SELECT canonical_job_id, title, company, location, description_md, primary_url, "  # noqa: S608
                f"compensation, seniority, min_years_experience, onsite_policy, clearance_required, "
                f"jd_keywords, jd_skills, legitimacy, date_posted, {cos_expr} AS cos_sim, "
                f"(SELECT count(DISTINCT site) FROM source_observations s "
                f" WHERE s.canonical_job_id = canonical_jobs.canonical_job_id) AS source_count, "
                f"(SELECT min(s.site) FROM source_observations s "
                f" WHERE s.canonical_job_id = canonical_jobs.canonical_job_id) AS site "
                f"FROM canonical_jobs WHERE canonical_job_id = ANY(:ids) "
                f"AND EXISTS (SELECT 1 FROM source_observations s "
                f" WHERE s.canonical_job_id = canonical_jobs.canonical_job_id)"
            ),
            {"ids": top_ids, **({"qvec": qvec} if qvec is not None else {})},
        )
        row_map = {r.canonical_job_id: dict(r._mapping) for r in rows}

    return top_ids, row_map


def rrf_fuse(legs: list[list[str]], k: int = RRF_K) -> list[str]:
    """Reciprocal Rank Fusion over ranked id lists: score = Σ 1/(k + rank) (docs/04 §3.1)."""
    scores: dict[str, float] = {}
    for leg in legs:
        for rank, jid in enumerate(leg):
            scores[jid] = scores.get(jid, 0.0) + 1.0 / (k + rank)
    return [jid for jid, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]
