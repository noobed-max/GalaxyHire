"""Search API surface (docs/04 §6, docs/07 §3). API-key guarded."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from galaxy.api.deps import require_api_key
from galaxy.api.routers.profile import _user
from galaxy.api.throttle import throttle
from galaxy.common.cache import search_cache
from galaxy.search import service
from galaxy.search.ranker import to_dict

router = APIRouter(tags=["search"], dependencies=[Depends(require_api_key), Depends(throttle)])


class StrictRequest(BaseModel):
    """Reject unknown fields instead of ignoring them.

    Pydantic's default is to drop fields it doesn't recognise, which on a search endpoint is a
    genuinely dangerous default: posting `{"query": "..."}` instead of `{"search_term": "..."}`
    silently becomes an *empty* search, and an empty search still returns a full page of
    profile-ranked jobs. The caller gets a plausible, confidently-scored, completely
    query-independent answer and no indication anything went wrong. Cost real debugging time
    before it was caught by eye, since nothing in the response looks off.

    Failing with a 422 turns that into an immediate, obvious error.
    """

    model_config = ConfigDict(extra="forbid")


class ParseRequest(StrictRequest):
    text: str


@router.post("/search/parse")
async def parse_route(req: ParseRequest, user_id: UUID = Depends(_user)) -> dict:
    """Compile a natural-language request into editable SearchRequest filter fields (docs/11 §W1.1).

    Never 502s — falls back to a deterministic parse when the LLM is unavailable.
    """
    from galaxy.search.nl_parse import parse_search

    return await parse_search(req.text)


class SearchRequest(StrictRequest):
    search_term: str | None = None
    positive_phrases: list[str] = []
    positive_skills: list[str] = []
    negative_titles: list[str] = []
    negative_phrases: list[str] = []
    max_years: int | None = None       # exclude jobs requiring MORE than this
    max_seniority: str | None = None   # exclude jobs more senior than this band
    remote: bool | None = None
    location: str | None = None
    blacklist: list[str] = []
    limit: int = 50
    sort: str = "relevance"  # "relevance" (fit) | "recent" (latest posting first)
    #: Product recency window. None = off (the caller decides); apps/api passes 24 by default so
    #: only source-proved-fresh jobs, or undated jobs new to the corpus, are ever served (R3).
    fresh_hours: int | None = None
    observed_after: str | None = None  # ISO; only jobs observed by the current scrape run


@router.post("/search")
async def search_route(req: SearchRequest, user_id: UUID = Depends(_user)) -> dict:
    # short-TTL cache keyed by (user, request) — repeated identical searches skip the work
    cache_key = hashlib.sha256(
        f"{user_id}:{json.dumps(req.model_dump(), sort_keys=True)}".encode()
    ).hexdigest()
    cached = search_cache.get(cache_key)
    if cached is not None:
        return cached

    ranked, quality, preference = await service.search_with_quality(
        user_id=user_id,
        search_term=req.search_term,
        positive_phrases=req.positive_phrases,
        positive_skills=req.positive_skills,
        negative_titles=req.negative_titles,
        negative_phrases=req.negative_phrases,
        max_years=req.max_years,
        max_seniority=req.max_seniority,
        remote=req.remote,
        location=req.location,
        blacklist=req.blacklist,
        limit=req.limit,
        sort=req.sort,
        fresh_hours=req.fresh_hours,
        observed_after=req.observed_after,
    )
    # `quality` says whether these results are answers or merely nearest neighbours. Retrieval
    # always returns its closest N, so without this a gibberish query looks identical to a good one.
    result = {
        "count": len(ranked),
        "results": [to_dict(j) for j in ranked],
        "quality": quality.as_dict(),
        # What the user's own feedback removed from this page. Always present, so a client can
        # render "hiding N you marked not relevant" rather than leaving them to wonder where a job
        # went — an invisible filter is indistinguishable from a thin corpus.
        "preference": preference.as_dict(),
    }
    search_cache.set(cache_key, result)
    return result


@router.get("/jobs/{canonical_job_id}")
async def get_job_route(canonical_job_id: str) -> dict:
    job = await service.get_job(canonical_job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


class FeedbackRequest(StrictRequest):
    canonical_job_id: str
    # Constrained rather than a bare str: an unrecognised signal used to insert happily and then
    # match nothing on the read side, so the user's click was accepted and silently discarded.
    signal: Literal["not_relevant", "too_senior", "too_junior", "good"]


@router.post("/feedback", status_code=204)
async def feedback_route(req: FeedbackRequest, user_id: UUID = Depends(_user)) -> None:
    await service.record_feedback(user_id, req.canonical_job_id, req.signal)
    # Results now depend on feedback, so the cached page for this query is stale the moment a
    # signal lands — without this, marking a job "not relevant" appears to do nothing for up to the
    # cache TTL, which reads as a broken button and invites the user to click it repeatedly.
    #
    # Clearing everything rather than one user's entries: the cache is keyed by an opaque hash, so
    # per-user eviction would mean tracking keys. A 120-second local cache and a rare, deliberate
    # user action make the blunt instrument the right one.
    search_cache.clear()


@router.get("/locations")
async def locations_route(q: str = "", limit: int = 12) -> dict:
    """Type-ahead for the location box, drawn from locations that actually have jobs."""
    return {"suggestions": await service.location_suggestions(q, limit=min(limit, 50))}


@router.get("/preference")
async def preference_route(user_id: UUID = Depends(_user)) -> dict:
    """What the user's accumulated feedback currently does to their searches.

    Exposed so the effect is inspectable — and reversible — rather than an opaque force acting on
    results. Without this the only way to discover you had trained a filter would be to notice
    something missing.
    """
    from galaxy.search.preference import load_preference

    return (await load_preference(user_id)).as_dict()


class ScrapeRequest(StrictRequest):
    """Start a collection run.

    `phrase`, `hours`, `location` and the selected `portals` — every other filter is applied
    afterwards over stored jobs (D7). Sending filters to the boards would mean re-scraping on
    every change of mind.
    """

    phrase: str
    hours: int = 24
    #: Passed to the job boards, unlike every other filter — they support it natively and answer
    #: far better for it. See ARCHITECTURE.md D7, narrowed to "location only".
    location: str = ""
    #: The resolved UI portal selection (ids from /scrape/sources). None = the config defaults.
    #: Part of the run's identity: freshness is only served by a superset run (runner §Freshness).
    portals: list[str] | None = None


@router.post("/scrape/start")
async def scrape_start_route(req: ScrapeRequest) -> dict:
    """Begin scraping `phrase` across the selected portals. Returns immediately with live state.

    If a scrape is already running this attaches to it rather than starting a second: two runs over
    the same corpus duplicate effort and double the load on every job board for no extra coverage.
    """
    from galaxy.scrape import runner

    try:
        return (
            await runner.start(
                req.phrase, hours=req.hours, location=req.location, portals=req.portals
            )
        ).as_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/scrape/fresh")
async def scrape_fresh_route(phrase: str = "", location: str = "", portals: str = "") -> dict:
    """Whether `phrase`+`location`+`portals` were collected recently enough to skip scraping.

    Searching is what triggers collection now, so without this every search would cost a full run
    — including pressing enter twice on the same phrase, or adjusting a filter.

    `portals` is a comma-separated list; freshness requires a RECORDED SUPERSET — a 3-portal run
    does not answer a 40-portal question (MAJOR-CHANGE/06 §5).
    """
    from galaxy.scrape import runner

    wanted = tuple(p.strip() for p in portals.split(",") if p.strip()) or None
    # Location is part of the key: the same phrase collected for India is not fresh for the UK.
    return {
        "fresh": await runner.freshly_scraped(phrase, location=location, portals=wanted),
        "phrase": phrase,
        "location": location,
        "portals": list(wanted or ()),
    }


@router.get("/scrape/sources")
async def scrape_sources_route() -> dict:
    """The portal catalog — every scrapable portal, its policy and default toggle state.

    Served from the scraper's own config file (galaxy/scrape/catalog.py), not from the corpus DB:
    the UI must list what CAN be scraped, not merely what returned jobs last time — otherwise a
    broken board silently disappears from the list and the failure becomes undeclarable.
    """
    from galaxy.scrape.catalog import load_catalog

    sources = load_catalog()
    return {"sources": sources, "total": len(sources)}


@router.get("/scrape/status")
async def scrape_status_route() -> dict:
    """Live state of the most recent run, read from the database.

    Read from storage rather than memory precisely so a browser refresh reattaches to a scrape in
    flight instead of showing an idle screen and inviting the user to start a second one.
    """
    from galaxy.scrape import runner

    return (await runner.current()).as_dict()


@router.post("/scrape/stop")
async def scrape_stop_route() -> dict:
    from galaxy.scrape import runner

    stopped = await runner.stop()
    from galaxy.scrape.runner import current

    return {"stopped": stopped, **(await current()).as_dict()}


@router.post("/scrape/history/clear")
async def scrape_history_clear_route() -> dict:
    """Clear user-authored scrape phrases while retaining deduplicated canonical jobs."""
    from galaxy.scrape import runner

    return await runner.clear_history()
