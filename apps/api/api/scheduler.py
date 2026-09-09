from __future__ import annotations
import logging

import asyncio
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from api.dependencies import get_corpus_discovery_service, get_generation_service, get_job_runner, get_ranking_service, get_repository
from core.config import job_search_brief
from core.search_intent import normalize_search_intent
from core.config import profile_for_discovery
from api.startup_validation import log_startup_warnings
from data.sqlite.connection import close_all, init_sql, prune_history


def create_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler()


def ensure_ghost_job(scheduler: AsyncIOScheduler, ghost_tick) -> None:
    if not scheduler.get_job("ghost"):
        # max_instances=1 + coalesce: a ghost cycle (search→eval→generate) can outlast the 6h
        # interval; never let a second copy start on top of a still-running one, and collapse any
        # ticks missed while it ran into one.
        scheduler.add_job(
            ghost_tick, "interval", hours=6, id="ghost",
            max_instances=1, coalesce=True,
        )


def ensure_email_job(scheduler: AsyncIOScheduler, email_tick) -> None:
    if not scheduler.get_job("email_monitor"):
        # 20 minutes: outcome emails are not time-critical, and each poll opens an IMAP connection.
        # max_instances=1 because a slow mailbox must not stack polls that re-scan the same window.
        scheduler.add_job(
            email_tick, "interval", minutes=20, id="email_monitor",
            max_instances=1, coalesce=True,
        )


def create_email_tick(manager):
    """Periodic email monitoring (§7).

    Self-disabling rather than conditionally registered: the job checks its own settings each run, so
    turning monitoring on in the UI takes effect without restarting the app.
    """

    async def email_tick():
        from email_monitor.service import create_email_monitor_service

        try:
            service = create_email_monitor_service()
            result = await asyncio.to_thread(service.poll)
        except Exception as exc:
            logging.getLogger(__name__).warning("email monitor tick failed: %s", exc)
            return
        if not result.enabled or result.error:
            return
        for outcome in result.outcomes:
            await manager.broadcast({
                "type": "agent",
                "event": "email_outcome",
                "msg": f"{outcome['company']}: {outcome['outcome']} ({outcome['confidence']} confidence)",
            })
        if result.updated:
            await manager.broadcast({"type": "LEADS_REFRESH", "data": {"reason": "email_outcomes"}})

    return email_tick


def create_ghost_tick(manager):
    """The background cycle: search the corpus, evaluate, generate assets — then stop.

    JustHireMe's version ended by *submitting* applications through a headless browser when
    `auto_apply` was on. That step is gone. Under ARCHITECTURE.md invariant 1 the extension fills
    and the human submits, so the most an unattended cycle may do is have everything ready and
    waiting for a person.

    Scraping is gone from here too. Jobs arrive in the corpus from `services/scraper-node` on its
    own schedule (D4/D7), so this cycle *retrieves* rather than scrapes — which is also what makes
    it cheap enough to run unattended.
    """

    async def ghost_tick():
        repo = get_repository()
        corpus = get_corpus_discovery_service()
        ranking_service = get_ranking_service()
        generation_service = get_generation_service()
        job_store = get_job_runner()

        cfg = repo.settings.get_settings()
        if repo.settings.get_setting("ghost_mode") != "true":
            return
        ghost_job = job_store.create("ghost_cycle", {})
        job_store.update(ghost_job.job_id, status="running", progress=5)

        profile = profile_for_discovery(await asyncio.to_thread(repo.profile.get_profile), cfg)
        raw_query = job_search_brief(cfg)
        if not raw_query:
            msg = "Ghost Mode: describe a target role before searching"
            await manager.broadcast({"type": "agent", "event": "ghost_warn", "msg": msg})
            job_store.update(ghost_job.job_id, status="cancelled", progress=100, error=msg)
            return

        # Background search follows the same boundary as the interactive path: the first positive
        # role phrase selects candidates, while exclusions are structured filters. The résumé is
        # available only to the later scoring step.
        parsed_query = await corpus.parse_query(raw_query)
        query, filters = normalize_search_intent(raw_query, parsed_query)
        if not query:
            msg = "Ghost Mode: no role to search for - set a target role in settings"
            await manager.broadcast({"type": "agent", "event": "ghost_warn", "msg": msg})
            job_store.update(ghost_job.job_id, status="cancelled", progress=100, error=msg)
            return

        await manager.broadcast(
            {"type": "agent", "event": "ghost_scout", "msg": f"Ghost Mode: searching the corpus for {query!r}"}
        )
        try:
            found = await corpus.search(
                query=query,
                profile=profile,
                settings=cfg,
                skills=filters.get("positive_skills"),
                negative_titles=filters.get("negative_titles"),
                negative_phrases=filters.get("negative_phrases"),
                max_years=filters.get("max_years"),
                max_seniority=filters.get("max_seniority"),
                remote=filters.get("remote"),
                location=filters.get("location"),
                rerank=False,
                limit=100,
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("ghost_tick corpus search failed: %s", exc)
            await manager.broadcast({"type": "agent", "event": "ghost_error", "msg": f"Corpus search failed: {exc}"})
            job_store.update(ghost_job.job_id, status="failed", error=str(exc))
            return

        if not found.corpus_available:
            msg = f"Ghost Mode: corpus unavailable - {found.note}"
            await manager.broadcast({"type": "agent", "event": "ghost_warn", "msg": msg})
            job_store.update(ghost_job.job_id, status="cancelled", progress=100, error=msg)
            return

        # Persist what came back so the pipeline and graph see it, exactly as scraped leads used to.
        for lead in found.leads:
            try:
                await asyncio.to_thread(repo.leads.save_lead, lead)
            except Exception as exc:
                logging.getLogger(__name__).warning("ghost_tick save_lead failed: %s", exc)

        await manager.broadcast(
            {"type": "agent", "event": "ghost_scout", "msg": f"Corpus search complete - {len(found.leads)} candidates"}
        )

        discovered = await asyncio.to_thread(repo.leads.get_discovered_leads)
        await manager.broadcast(
            {"type": "agent", "event": "ghost_eval", "msg": f"Ghost Mode: evaluating {len(discovered)} leads"}
        )

        # Token gate: LLM-evaluate only the top-K by the cheap deterministic score, so an
        # unattended run can't quietly burn tokens on the whole backlog.
        from core.config import int_cfg

        ghost_max_llm = int_cfg(cfg, "ghost_max_llm_evaluations", 15, 0, 500)
        llm_ids = await ranking_service.select_llm_eval_ids(discovered, profile, max_llm=ghost_max_llm)

        approved = []
        for lead in discovered:
            try:
                result = await ranking_service.evaluate_lead(lead, profile, cfg, use_llm=lead["job_id"] in llm_ids)
                # Background re-scoring must not overwrite a status the user changed
                # (approved/applied/interviewing) during this slow loop.
                await asyncio.to_thread(
                    repo.leads.update_lead_score,
                    lead["job_id"], result["score"], result["reason"],
                    result.get("match_points", []), result.get("gaps", []),
                    preserve_status=True,
                )
                await manager.broadcast({"type": "LEAD_UPDATED", "data": {**lead, **result}})
                if result["score"] >= 85:
                    approved.append({**lead, **result})
                    await manager.broadcast({
                        "type": "agent",
                        "event": "ghost_approved",
                        "msg": f"Approved: {lead.get('title','')} @ {lead.get('company','')} [{result['score']}/100]",
                    })
            except Exception as exc:
                logging.getLogger(__name__).warning("ghost_tick eval failed: %s", exc)
                await manager.broadcast(
                    {"type": "agent", "event": "ghost_error", "msg": f"Eval failed for {lead.get('title','?')}: {exc}"}
                )

        await manager.broadcast(
            {"type": "agent", "event": "ghost_eval", "msg": f"Evaluation done - {len(approved)}/{len(discovered)} approved"}
        )

        if not approved:
            await manager.broadcast({"type": "agent", "event": "ghost_done", "msg": "Ghost Mode: no approved leads this cycle"})
            job_store.update(ghost_job.job_id, status="succeeded", progress=100, result={"approved": 0})
            return

        await manager.broadcast(
            {"type": "agent", "event": "ghost_gen", "msg": f"Ghost Mode: generating assets for {len(approved)} leads"}
        )
        generated = 0
        for lead in approved:
            try:
                package = await generation_service.generate_package(lead)
                await asyncio.to_thread(
                    repo.leads.save_asset_package,
                    lead["job_id"],
                    package["resume"],
                    package["cover_letter"],
                    package.get("selected_projects", []),
                    package.get("keyword_coverage", {}),
                )
                generated += 1
                await manager.broadcast(
                    {"type": "agent", "event": "ghost_gen", "msg": f"Generated resume and cover letter for {lead.get('title','?')}"}
                )
            except Exception as exc:
                logging.getLogger(__name__).warning("ghost_tick generation failed: %s", exc)
                await manager.broadcast(
                    {"type": "agent", "event": "ghost_error", "msg": f"Generation failed for {lead.get('title','?')}: {exc}"}
                )

        # The cycle ends here, always. Nothing auto-applies.
        await manager.broadcast({
            "type": "agent",
            "event": "ghost_done",
            "msg": f"Ghost cycle complete - {generated} leads ready to review. You apply; the extension fills.",
        })
        job_store.update(
            ghost_job.job_id, status="succeeded", progress=100,
            result={"approved": len(approved), "generated": generated},
        )

    return ghost_tick


def create_lifespan(scheduler: AsyncIOScheduler, ghost_tick, logger, email_tick=None):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_sql()
        prune_history()  # cap the append-only telemetry tables on startup
        ensure_ghost_job(scheduler, ghost_tick)
        if email_tick is not None:
            ensure_email_job(scheduler, email_tick)
        log_startup_warnings(get_repository(), logger)
        scheduler.start()

        # Warm real semantic embeddings in the background: auto-download the ONNX model
        # if it's missing so ranking uses meaning-level fit (not the near-random hash
        # fallback) by default, with no manual setup. Non-blocking — the scan uses hash
        # until the model is ready, then upgrades automatically.
        async def _warm_embeddings() -> None:
            try:
                from data.vector.embeddings import ensure_onnx_model
                active = await asyncio.to_thread(ensure_onnx_model)
                logger.info("embedding warm-up done (onnx active=%s)", active)
            except Exception as exc:
                logger.warning("embedding warm-up skipped: %s", exc)

        warm_task = asyncio.create_task(_warm_embeddings())
        logger.info("FastAPI live.")
        try:
            yield
        finally:
            warm_task.cancel()
            scheduler.shutdown(wait=False)
            from api.ingestion_tasks import reset_ingestion_task_manager

            # Drain ingestion workers (including outstanding to_thread SQLite
            # calls) before the shared connection pool is closed.
            await asyncio.to_thread(reset_ingestion_task_manager)
            close_all()
        logger.info("FastAPI shutdown.")

    return lifespan
