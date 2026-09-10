"""Discovery — broad role collection followed by corpus retrieval.

JustHireMe's discovery router drove its own scraper stack (`discovery/` plus the `automation/`
scouts) and has been removed wholesale per the brief. This replacement keeps the same HTTP surface,
asks the corpus-owned career-ops scraper to collect the positive role across the toggled portals,
and then searches:

    collection  services/scraper-node  -> corpus         (triggered here, owned by corpus)
    filtering   this router            -> corpus         (after collection, D7)

The separation is the point of D7. Collection is broad and role-level; seniority/location/negative
filters never reach a board and instead narrow the stored corpus afterwards. The role phrase itself
*is* sent — boards with a server-side search get it (query injection), the rest gate titles
client-side at ingest (MAJOR-CHANGE/05 §4).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_corpus_discovery_service, get_job_runner, get_repository
from api.search_runtime import SEARCH_TASK, search_tasks
from corpus.service import DEFAULT_RERANK_LIMIT
from core.logging import get_logger
from core.config import job_search_brief
from core.search_intent import normalize_search_intent
from core.config import profile_for_discovery

_log = get_logger(__name__)

# Statuses a background re-evaluation must never overwrite: the user changed them by hand, and a
# slow scoring loop finishing afterwards should not silently undo that.
REEVALUATION_STATUS_LOCKS = {"approved", "applied", "interviewing", "rejected", "accepted", "discarded"}


def should_preserve_job_status(status: str) -> bool:
    return status in REEVALUATION_STATUS_LOCKS



class CorpusFeedbackRequest(BaseModel):
    """A judgement on one search result.

    `signal` is a Literal, not a str: the corpus stores whatever it is given but only acts on the
    four it recognises, so a typo here would be accepted, persisted, and then quietly ignored on
    every subsequent search.
    """

    canonical_job_id: str
    signal: Literal["not_relevant", "too_senior", "too_junior", "good"]


class SearchRequest(BaseModel):
    """The positive role is collected broadly; every structured field filters afterwards."""

    query: str | None = None
    max_seniority: str | None = None
    max_years: int | None = None
    remote: bool | None = None
    location: str | None = None
    #: Portal ids to collect from (the UI toggle column). None = the saved selection; an
    #: explicit list is "this run only". Validated against the scraper's own catalog.
    portals: list[str] | None = None
    # The current-search view has no paging, so this default is the complete visible result set.
    # It was 50 against a ~5,000-job corpus, which looked like the product could only see 50 jobs.
    limit: int = DEFAULT_RERANK_LIMIT
    rerank: bool = True
    use_llm: bool = False
    # The dashboard's explicit Find action requests a new collection even when this exact
    # question was scraped recently.  Background/scheduled searches leave this false and retain
    # the freshness cache; making the intent explicit avoids globally disabling that protection.
    force_scrape: bool = False


class PortalSelectionRequest(BaseModel):
    """The saved toggle map: {portal_id: bool}. Empty dict means 'use the config defaults'."""

    portals: dict[str, bool] = {}


class ScrapeStartRequest(BaseModel):
    """Only the phrase, the window and the portal set. Filters are applied afterwards over
    stored jobs (D7)."""

    phrase: str
    hours: int = 24
    portals: list[str] | None = None


def _query_for(profile: dict, cfg: dict, explicit: str | None) -> str:
    """Choose only a user-authored search brief.

    ``profile`` remains in the signature for compatibility with older callers, but is intentionally
    ignored. Résumé facts may re-rank eligible results later; they may not decide what gets scraped.
    """
    del profile
    return str(explicit or "").strip() or job_search_brief(cfg)


async def _resolve_portals(
    corpus, cfg: dict, explicit: list[str] | None
) -> tuple[list[str] | None, str | None]:
    """Resolve which portals a search collects from, against the scraper's own catalog.

    Precedence per portal: an explicit request list ("this run only") > the saved toggle map
    (`scrape_portals` setting) > every currently selectable catalog entry. A saved map that
    predates a portal does not vote on the new source, so it starts enabled and the next UI save
    writes the complete truth map.

    Unknown ids in an explicit list are refused rather than dropped: a toggle that silently
    stops corresponding to a real portal is the same class of bug as an invisible filter.
    Stale keys lingering in a saved map are simply ignored (the UI rewrites the full map on
    the next toggle) — blocking every search until then would punish the user for a rename
    they never made. Returns (portals, error).
    """
    catalog = await corpus.scrape_sources()
    sources = catalog.get("sources", [])
    selectable = [
        source for source in sources
        if source.get("id") and source.get("recency_policy") != "off"
    ]
    known = {source["id"] for source in selectable}
    if not known:
        # No catalog (corpus down or config missing): pass an explicit list through untouched —
        # the scraper validates ids hard — and let stored jobs serve an unlocated search rather
        # than guessing a selection from a dead catalog.
        return explicit, None
    if explicit is not None:
        chosen = explicit
    else:
        raw = str(cfg.get("scrape_portals") or "").strip()
        if not raw:
            # A first run should cover everything the UI can turn on. Resolve it HERE rather than
            # leaving None: the run must record the exact set it covered, or the superset
            # freshness rule has nothing to compare against.
            return sorted(source["id"] for source in selectable), None
        try:
            saved = json.loads(raw)
        except ValueError:
            return None, "Saved portal selection is corrupt; re-save it from the portals panel."
        if not isinstance(saved, dict):
            return None, "Saved portal selection is corrupt; re-save it from the portals panel."
        if not saved:
            return sorted(source["id"] for source in selectable), None
        chosen = [source["id"] for source in selectable if saved.get(source["id"], True)]
    if not chosen:
        return None, "Turn on at least one portal before searching."
    unknown = sorted(set(chosen) - known)
    if unknown:
        return None, f"Unknown portal id(s): {', '.join(unknown)}"
    return sorted(chosen), None


async def _compile_constraints(corpus, text: str, req: SearchRequest) -> tuple[str, dict]:
    """Split a plain-English brief into a search term plus the filters it implies.

    The "what you're looking for" box invites sentences like *"software engineer, no 3+ years of
    experience, no SDE 2, no SDE 3"*. That whole string used to be handed to retrieval as the
    **search term**, which inverts the user's meaning in the worst possible way: the embedder has no
    notion of "no", so "SDE 2" and "SDE 3" became things to match *towards*. Asking to exclude
    senior roles actively pulled them to the top of the page.

    `parse_search` on the corpus already compiles English into SearchRequest fields and falls back
    to a deterministic parse when no LLM is configured, so this is wiring, not new machinery.

    Explicit request fields always win — a dropdown the user set is a stronger statement than a
    clause parsed out of prose.
    """
    if not text.strip():
        return "", {}
    try:
        compiled = await corpus.parse_query(text)
    except Exception as exc:  # noqa: BLE001 - a failed parse must not fail the search
        _log.warning("query compile failed, using deterministic constraints: %s", exc)
        compiled = {}

    query, filters = normalize_search_intent(text, compiled)
    for field in ("max_seniority", "max_years", "remote", "location"):
        # An explicit request field is stronger than a clause inferred from prose.
        explicit = getattr(req, field, None)
        if explicit is not None:
            if field == "location" and isinstance(explicit, str) and not explicit.strip():
                # Explicit empty location overrides and clears any location inferred from text
                filters.pop("location", None)
            else:
                filters[field] = explicit
    return query, filters


#: How often to ask the corpus whether the scrape has finished.
#:
#: There is deliberately **no duration limit** here. A run takes as long as it takes — how long
#: depends on the phrase, how many of the selected portals have anything for it, and the network — so any
#: fixed deadline is a guess that eventually cuts off a healthy scrape. An earlier version capped it
#: at 30 minutes on the basis that a typical run is 10-20; that reasoning turns an observation into
#: a rule and breaks the first run that is merely slower than typical.
#:
#: The loop instead follows the run's real state. The corpus is the single source of truth for
#: whether a scrape is alive: it checks the process group on every status read and finalises runs
#: whose process is gone, and `_reap_stale` closes out anything abandoned by a crash. So "still
#: running" always resolves eventually, without this layer holding an opinion about the clock.
SCRAPE_POLL_S = 3.0


async def _scrape_for(
    corpus,
    phrase: str,
    stop,
    broadcast,
    location: str = "",
    portals: list[str] | None = None,
    force_scrape: bool = False,
) -> dict:
    """Collect `phrase` from the job boards, then return so the caller can filter what arrived.

    This is the "search triggers the scrape" behaviour: there is no separate collection step and no
    default keyword list. The positive role phrase gets scraped, **and the location with it**.

    Location is the one filter that goes to the boards. They support it natively and answer far
    better for it — LinkedIn returns 600 India-specific software roles for "software engineer" +
    India, where an unlocated query returns a global spread that filtering afterwards mostly
    discards. Everything else (seniority, negatives, years) stays post-hoc, because boards either
    ignore those or apply them inconsistently. ARCHITECTURE.md D7, narrowed to "location only".

    Blocking is deliberate — the alternative offered was streaming results in as boards reported,
    and waiting with visible progress was chosen instead. Progress is broadcast over the websocket
    so the UI's indicator moves rather than showing a bare spinner.

    A user-triggered collection failure is returned to the caller. The dashboard must not turn a
    scraper outage into a plausible-looking page of jobs from an older corpus run.
    """
    outcome = {
        "started": False,
        "fresh_skipped": False,
        "completed": False,
        "failed": False,
        "stopped": False,
        "error": "",
        "started_at": "",
        "collected": 0,
        "deduplicated": 0,
        "conflict": False,
    }
    if not phrase.strip():
        return outcome
    try:
        # Do this read before the start call as a compatibility guard for older corpus workers
        # that do not return the explicit ``conflict`` marker. The runner still arbitrates the
        # start atomically, so this is only a clearer early status, not the lock itself.
        status_reader = getattr(corpus, "scrape_status", None)
        if force_scrape and status_reader is not None:
            try:
                active = await status_reader()
            except Exception:  # noqa: BLE001 - the start call remains the authoritative fallback
                active = {}
            if active.get("running"):
                outcome["conflict"] = True
                await broadcast(
                    "scan_warn",
                    "A scrape is already running; this Find action did not start a second scraper",
                )
                return outcome
        if not force_scrape and await corpus.scrape_fresh(phrase, location, portals):
            # Collected within the freshness window — searching immediately is the whole point of
            # storing what we scrape.
            outcome["fresh_skipped"] = True
            await broadcast("scan_info", "Using a recent scrape; no new collection was needed")
            return outcome

        await broadcast(
            "scan_info",
            f"Starting a fresh scrape for {phrase!r}"
            + (f" in {location}" if location else "")
            + (f" across {len(portals)} portals" if portals else "")
            + "…",
        )
        scrape_started_at = datetime.now(UTC).isoformat()
        started = await corpus.scrape_start(phrase, location=location, portals=portals)
        if not started.get("available", True):
            outcome["failed"] = True
            outcome["error"] = str(started.get("error") or "The scraper is unavailable")
            await broadcast("scan_error", f"Fresh collection failed: {outcome['error']}")
            return outcome
        if started.get("conflict"):
            outcome["conflict"] = True
            await broadcast(
                "scan_warn",
                "A scrape is already running; this Find action did not start a second scraper",
            )
            return outcome

        outcome["started"] = True
        outcome["started_at"] = scrape_started_at
        where = f" in {location}" if location else ""
        how = f" from {len(portals)} portals" if portals else " from the job boards"
        await broadcast("scan_info", f"Fresh scrape collecting {phrase!r}{where}{how}…")
        while True:
            if stop is not None and stop.is_set():
                # Stopping a search must stop the collection it started, or the user presses stop
                # and every selected portal carries on hammering job boards unattended.
                await corpus.scrape_stop()
                outcome["stopped"] = True
                return outcome
            await asyncio.sleep(SCRAPE_POLL_S)
            state = await corpus.scrape_status()
            if state.get("available") is False:
                outcome["failed"] = True
                outcome["error"] = str(state.get("error") or "The scraper status is unavailable")
                await broadcast("scan_error", f"Fresh collection failed: {outcome['error']}")
                return outcome
            if not state.get("running"):
                outcome["collected"] = int(state.get("collected", 0) or 0)
                # An explicit stopped state means the user cancelled the collection; don't
                # retire rows based on a partial snapshot.
                terminal_status = state.get("status", "done")
                outcome["completed"] = terminal_status not in {"failed", "stopped"}
                outcome["stopped"] = terminal_status == "stopped"
                outcome["failed"] = terminal_status == "failed"
                outcome["error"] = str(state.get("error") or "")
                if outcome["failed"]:
                    await broadcast("scan_error", f"Fresh collection failed: {outcome['error'] or 'scraper failed'}")
                elif outcome["stopped"]:
                    await broadcast("scan_warn", "Fresh collection stopped before completion")
                await broadcast(
                    "scan_info",
                    f"Fresh scrape collected {outcome['collected']} jobs",
                )
                return outcome
    except Exception as exc:  # noqa: BLE001 - surfaced as an explicit search failure
        _log.warning("scrape for %r failed: %s", phrase, exc)
        outcome["failed"] = True
        outcome["error"] = str(exc) or "The scraper failed to start"
        await broadcast("scan_error", f"Fresh collection failed: {outcome['error']}")
        return outcome


def create_router(manager=None, logger=None) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["discovery"])
    # Reuses JustHireMe's TaskRegistry so the UI's stop button keeps working and two searches
    # can't run concurrently. A plain boolean flag would allow both.
    tasks = search_tasks
    SEARCH = SEARCH_TASK
    last: dict[str, dict] = {}

    async def _broadcast(event: str, msg: str) -> None:
        if manager is not None:
            await manager.broadcast({"type": "agent", "event": event, "msg": msg})

    async def _run_search(req: SearchRequest, stop: asyncio.Event | None = None) -> dict:
        repo = get_repository()
        corpus = get_corpus_discovery_service()
        job_store = get_job_runner()

        cfg = repo.settings.get_settings()
        profile = profile_for_discovery(await asyncio.to_thread(repo.profile.get_profile), cfg)
        raw_query = _query_for(profile, cfg, req.query)
        if not raw_query:
            return {
                "ok": False,
                "error": "Describe the role you want before starting a search.",
            }

        query, filters = await _compile_constraints(corpus, raw_query, req)
        if not query:
            return {
                "ok": False,
                "error": "Add a positive role before exclusions, for example “software engineer, no senior”.",
            }

        job = job_store.create("corpus_search", {"query": query})
        job_store.update(job.job_id, status="running", progress=10)
        await _broadcast("scan_start", f"Starting a fresh job search for {query!r}")

        # Collect first, then filter the jobs observed by this run. The phrase the user typed is
        # what goes to the job boards — nothing else, per D7 — and changing a filter never
        # re-scrapes.
        #
        # Scheduled/background searches skip a recent matching scrape. The dashboard's explicit
        # Find action sets force_scrape, which deliberately bypasses that cache for every click.
        # Resolve the portal selection BEFORE scraping: an invalid or empty selection is a user
        # error to surface, not something to scrape around.
        portals, portal_err = await _resolve_portals(corpus, cfg, req.portals)
        if portal_err:
            message = str(portal_err)
            job_store.update(job.job_id, status="failed", progress=100, error=message)
            return {"ok": False, "error": message, "query": query, "filters": filters}

        # The location filter is the only one handed to the boards; the rest narrow stored jobs.
        if req.location is not None:
            scrape_location = req.location.strip()
        else:
            scrape_location = str(filters.get("location") or "").strip()
        scrape_outcome = await _scrape_for(
            corpus,
            query,
            stop,
            _broadcast,
            location=scrape_location,
            portals=portals,
            force_scrape=req.force_scrape,
        )
        if scrape_outcome.get("conflict"):
            message = "A scrape is already running; wait for it to finish before starting another Find action."
            job_store.update(job.job_id, status="failed", progress=100, error=message)
            return {
                "ok": False,
                "conflict": True,
                "error": message,
                "status": "running",
                "query": query,
                "filters": filters,
            }
        if stop is not None and stop.is_set():
            job_store.update(job.job_id, status="cancelled", progress=100)
            await _broadcast("scan_stop", "Search cancelled")
            return {"ok": False, "cancelled": True, "query": query, "filters": filters}

        # An explicit dashboard Find action must never fall back to stale corpus rows when its
        # collection failed or was stopped. That made a broken scraper look like a successful new
        # search. Background callers retain their historical best-effort behavior.
        if req.force_scrape and not scrape_outcome.get("fresh_skipped"):
            if scrape_outcome.get("failed"):
                error = scrape_outcome.get("error") or "Fresh collection failed"
                job_store.update(job.job_id, status="failed", progress=100, error=error)
                return {
                    "ok": False, "error": error, "query": query, "filters": filters,
                    "scrape": scrape_outcome, "leads": [], "retrieved": 0,
                }
            if scrape_outcome.get("stopped") or not scrape_outcome.get("completed"):
                error = "Fresh collection did not complete; no stale jobs were returned."
                job_store.update(job.job_id, status="failed", progress=100, error=error)
                return {
                    "ok": False, "error": error, "query": query, "filters": filters,
                    "scrape": scrape_outcome, "leads": [], "retrieved": 0,
                }

        if filters:
            # Say what was inferred from the sentence. A filter the user did not set explicitly and
            # cannot see is indistinguishable from the corpus simply not having those jobs.
            await _broadcast("scan_info", f"Filtering newly collected jobs: {filters}")

        result = await corpus.search(
            query=query,
            profile=profile,
            settings=cfg,
            skills=filters.get("positive_skills"),
            max_seniority=filters.get("max_seniority"),
            max_years=filters.get("max_years"),
            remote=filters.get("remote"),
            location=filters.get("location") or None,
            negative_titles=filters.get("negative_titles"),
            negative_phrases=filters.get("negative_phrases"),
            limit=req.limit,
            rerank=req.rerank,
            use_llm=req.use_llm,
            observed_after=(
                scrape_outcome.get("started_at")
                if req.force_scrape and scrape_outcome.get("completed")
                else None
            ),
        )

        if not result.corpus_available:
            job_store.update(job.job_id, status="failed", error=result.note or "corpus unavailable")
            await _broadcast("scan_error", result.note or "corpus unavailable")
            return {"ok": False, "error": result.note, "corpus_available": False}

        if stop is not None and stop.is_set():
            # Checked after retrieval and before the writes: stopping should abandon the results,
            # not leave the pipeline half-populated.
            job_store.update(job.job_id, status="cancelled", progress=100)
            await _broadcast("scan_stop", "Search cancelled")
            return {"ok": False, "cancelled": True, "query": query, "filters": filters}

        saved = 0
        deduplicated = 0
        retired = 0
        # The corpus has already deduplicated observations into canonical jobs. This second
        # boundary is still necessary: the local pipeline may contain an older canonical id for
        # the same normalized source URL, and one retrieval can contain duplicate rows after a
        # partial/replayed ingest.
        save_helper = getattr(repo.leads, "save_discovery_leads", None)
        if save_helper is None:
            # Compatibility for an external/legacy repository implementation. The shipped store
            # always takes the deduplicating path above; this fallback is intentionally narrow so
            # a retirement error never causes already-saved rows to be inserted a second time.
            for lead in result.leads:
                try:
                    inserted = await asyncio.to_thread(repo.leads.save_lead, lead)
                    saved += int(inserted is not False)
                except Exception as save_exc:  # noqa: BLE001 - one bad row must not lose the page
                    _log.warning("save_lead failed for %s: %s", lead.get("job_id"), save_exc)
        else:
            try:
                saved_result = await asyncio.to_thread(
                    save_helper,
                    result.leads,
                    query=query,
                    location=scrape_location,
                    portals=portals,
                )
                saved = int(saved_result.get("saved", 0) or 0)
                deduplicated = int(saved_result.get("deduplicated", 0) or 0)
            except Exception as exc:  # noqa: BLE001 - one bad row must not lose the page
                _log.warning("save discovery leads failed: %s", exc)

        retire_helper = getattr(repo.leads, "retire_stale_discovery_leads", None)
        # Retirement needs a known, non-empty portal set: a run whose coverage is unknown (catalog
        # unavailable, so `_resolve_portals` passed None through) cannot archive rows from boards it
        # may never have asked. The store enforces the same rule; this guard avoids the DB round trip.
        if scrape_outcome.get("completed") and portals and retire_helper is not None:
            try:
                retirement = await asyncio.to_thread(
                    retire_helper,
                    query=query,
                    location=scrape_location,
                    portals=portals,
                    fresh_leads=result.leads,
                )
                retired = int(retirement.get("retired", 0) or 0)
            except Exception as exc:  # noqa: BLE001 - retirement is safety-bounded best effort
                _log.warning("retire stale discovery leads failed: %s", exc)

        # `note` is how the caller learns *why* a result set is thin — empty corpus, still
        # embedding, or nothing matching the words typed. Those look identical otherwise.
        if result.note:
            await _broadcast("scan_warn", result.note)
        await _broadcast(
            "scan_done",
            f"Fresh scrape complete: collected {scrape_outcome.get('collected', 0)}, "
            f"found {result.retrieved} candidates, {saved} new, "
            f"{deduplicated} deduplicated, {retired} retired",
        )
        job_store.update(
            job.job_id, status="succeeded", progress=100,
            result={
                "retrieved": result.retrieved,
                "reranked": result.reranked,
                "saved": saved,
                "deduplicated": deduplicated,
                "retired": retired,
                "scrape": scrape_outcome,
            },
        )
        return {
            "ok": True,
            "query": query,
            "brief": raw_query,
            "portals": portals,
            "filters": filters,
            "retrieved": result.retrieved,
            "reranked": result.reranked,
            "query_matches": result.query_matches,
            "saved": saved,
            "deduplicated": deduplicated,
            "retired": retired,
            "scrape": scrape_outcome,
            "note": result.note,
            "leads": result.leads,
        }

    async def _start(req: SearchRequest) -> dict:
        """Run a search under the registry so it is cancellable and non-overlapping."""
        started = await tasks.start(
            SEARCH, lambda stop: _capture(req, stop)
        )
        if not started:
            return {
                "ok": False,
                "conflict": True,
                "error": "A search/scrape is already running; wait for it to finish before starting another.",
                "status": "running",
            }
        return {"ok": True, "started": True, "query": req.query}

    async def _capture(req: SearchRequest, stop: asyncio.Event) -> None:
        try:
            last[SEARCH] = await _run_search(req, stop)
        except Exception as exc:  # noqa: BLE001 - the synchronous route still needs a terminal result
            _log.exception("corpus search failed")
            last[SEARCH] = {"ok": False, "error": str(exc) or "search failed"}
            await _broadcast("scan_error", "Search failed")

    @router.post("/scan")
    async def scan(req: SearchRequest | None = None) -> dict:
        """Collect the positive role, then search the corpus with post-collection filters.

        Still synchronous — the UI awaits the result — but now run **through the task registry**
        rather than beside it.

        That is what makes "Stop scan" work. This used to call `_run_search` directly, so no task
        was ever registered under `SEARCH`; `/scan/stop` then looked for a running task, found
        none, and returned "No search running" while a search was plainly in progress. The button
        had never done anything, for anyone. Registering here means the same stop event
        `_run_search` already checks can actually be set.
        """
        request = req or SearchRequest()
        if not await tasks.start(SEARCH, lambda stop: _capture(request, stop)):
            # A dashboard Find request is an explicit user action. Surface a real conflict so it
            # cannot look like a successful click that merely returned cached jobs; background
            # callers retain the older structured-error response for compatibility.
            if request.force_scrape:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "A search/scrape is already running; wait for it to finish before starting another Find action.",
                        "status": "running",
                        "conflict": True,
                    },
                )
            return {
                "ok": False,
                "conflict": True,
                "error": "A search/scrape is already running; wait for it to finish before starting another.",
                "status": "running",
            }
        await tasks.join(SEARCH)
        result = last.get(SEARCH) or {"ok": False, "error": "search produced no result"}
        if request.force_scrape and result.get("conflict"):
            raise HTTPException(status_code=409, detail=result)
        return result

    @router.post("/scan/background")
    async def scan_background(req: SearchRequest | None = None) -> dict:
        return await _start(req or SearchRequest())

    @router.get("/scan/result")
    async def scan_result() -> dict:
        task_running = await tasks.is_running(SEARCH)
        # The connector process is persisted by the corpus and may outlive an API restart. Looking
        # only at this process's in-memory TaskRegistry made a refreshed UI say idle while scraping
        # continued, or leave the Stop button in the wrong state after reconciliation.
        scrape = await get_corpus_discovery_service().scrape_status()
        scrape_running = bool(scrape.get("running"))
        return {
            "running": task_running or scrape_running,
            "search_running": task_running,
            "scrape_running": scrape_running,
            "scrape": scrape,
            "result": last.get(SEARCH),
        }

    @router.post("/scan/stop")
    async def stop_scan() -> dict:
        stopped = await tasks.stop(SEARCH)
        # Stop the corpus process immediately as well as setting the cooperative task event. This
        # also recovers an orphaned scrape after an API restart, when no in-memory task exists but
        # the connector process is still running.
        scrape = await get_corpus_discovery_service().scrape_stop()
        scrape_stopped = bool(scrape.get("stopped"))
        any_stopped = stopped or scrape_stopped
        await _broadcast("scan_stop", "Stopping search" if any_stopped else "No search running")
        return {
            "ok": any_stopped,
            "search_stopped": stopped,
            "scrape_stopped": scrape_stopped,
        }

    @router.post("/free-sources/scan")
    async def free_sources_scan(req: SearchRequest | None = None) -> dict:
        """Kept for UI compatibility.

        "Free sources" was a distinction in JustHireMe's scraper between keyless boards and
        API-key ones. It has no meaning now: every job in the corpus arrived through the unified
        connector set, so this is the same search.
        """
        return await _run_search(req or SearchRequest())

    @router.get("/corpus/stats")
    async def corpus_stats(corpus=Depends(get_corpus_discovery_service)) -> dict:
        """Corpus size and how much of it is searchable.

        The two differ while a scrape's jobs are still being embedded, and that gap is the
        difference between "no jobs" and "jobs nobody can query yet".
        """
        return await corpus.stats()

    @router.post("/corpus/feedback")
    async def corpus_feedback(
        req: CorpusFeedbackRequest, corpus=Depends(get_corpus_discovery_service)
    ) -> dict:
        """Record a judgement on a search result, and return the resulting preference.

        Returns the new preference rather than 204 so the client can show the effect immediately —
        "hiding 4 jobs you marked not relevant" — instead of leaving the user to infer it from a
        result list that quietly changed.
        """
        return await corpus.record_feedback(req.canonical_job_id, req.signal)

    @router.get("/corpus/locations")
    async def corpus_locations(q: str = "", corpus=Depends(get_corpus_discovery_service)) -> dict:
        """Suggest locations that have jobs, for the free-text location box."""
        return await corpus.locations(q)

    @router.get("/corpus/sources")
    async def corpus_sources(corpus=Depends(get_corpus_discovery_service)) -> dict:
        """The portal catalog for the toggle column, from the scraper's own config.

        Never derived from the corpus DB: the UI must list what CAN be scraped, not merely what
        returned jobs last time — otherwise a broken board silently disappears from the list and
        its failure becomes undeclarable (MAJOR-CHANGE/06 §2)."""
        return await corpus.scrape_sources()

    @router.get("/corpus/portals")
    async def get_portal_selection(repo=Depends(get_repository)) -> dict:
        """The user's saved toggle map ({} when unset — meaning catalog defaults)."""
        return {"portals": await asyncio.to_thread(repo.settings.get_setting, "scrape_portals", "")}

    @router.post("/corpus/portals")
    async def save_portal_selection(req: PortalSelectionRequest, repo=Depends(get_repository)) -> dict:
        """Store the toggle map. Validated against the catalog so a renamed portal can't linger
        as a dead toggle that silently selects nothing."""
        corpus = get_corpus_discovery_service()
        known = {s["id"] for s in (await corpus.scrape_sources()).get("sources", [])}
        if known:
            unknown = sorted(set(req.portals) - known)
            if unknown:
                return {"ok": False, "error": f"unknown portal ids: {', '.join(unknown)}"}
        await asyncio.to_thread(
            repo.settings.save_settings, {"scrape_portals": json.dumps(req.portals)}
        )
        return {"ok": True, "count": len(req.portals)}

    @router.post("/corpus/scrape/start")
    async def scrape_start(req: ScrapeStartRequest, corpus=Depends(get_corpus_discovery_service)) -> dict:
        """Collect `phrase` across the selected portals. Returns immediately with live state."""
        return await corpus.scrape_start(req.phrase, req.hours, portals=req.portals)

    @router.get("/corpus/scrape/status")
    async def scrape_status(corpus=Depends(get_corpus_discovery_service)) -> dict:
        """Polled by the progress indicator. Read from storage, so a refresh reattaches."""
        return await corpus.scrape_status()

    @router.post("/corpus/scrape/stop")
    async def scrape_stop(corpus=Depends(get_corpus_discovery_service)) -> dict:
        return await corpus.scrape_stop()

    @router.get("/corpus/preference")
    async def corpus_preference(corpus=Depends(get_corpus_discovery_service)) -> dict:
        """The standing effect of this user's feedback, so it can be inspected rather than guessed."""
        return await corpus.preference()

    return router
