"""Ingestion pipeline — the corpus write path (docs/01 §6A, docs/02).

The only entry point is `run_observations`: the Node scraper worker fetches through the
vendored career-ops providers and posts envelopes to /ingest/observations, which land here for
dedup + upsert. The old in-process adapter path (`run`, `IngestJob`, `_run_one`, browser
fan-out) deleted with the GalaxyJobsAi Python adapters — career-ops is the connector set now,
and it runs in its native runtime, not here. Ingestion stays role-agnostic: relevance is a
retrieval concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from galaxy.common import metrics
from galaxy.dedup.engine import DedupEngine, DedupStats
from galaxy.ingestion.normalize import apply_derived_fields
from galaxy.ingestion.store import JobStore
from galaxy.models.job import SourceObservation

log = structlog.get_logger(__name__)


@dataclass
class IngestReport:
    fetched: int = 0
    upserted: int = 0
    failures: list[str] = field(default_factory=list)
    dedup: DedupStats | None = None


class IngestPipeline:
    def __init__(self, store: JobStore):
        self.store = store
        self.dedup = DedupEngine()

    async def _finalize(self, all_obs: list[SourceObservation], report: IngestReport) -> IngestReport:
        """Dedup a collected observation set and upsert it — the half of the write path that
        doesn't care where the observations came from."""
        report.fetched = len(all_obs)

        dedup_result = self.dedup.dedup(all_obs)
        report.dedup = dedup_result.stats
        report.upserted = await self.store.upsert_jobs(dedup_result.jobs)

        log.info(
            "ingest.complete",
            fetched=report.fetched,
            canonical=dedup_result.stats.canonical_out,
            split_groups=dedup_result.stats.split_groups,
            upserted=report.upserted,
            failures=len(report.failures),
        )
        return report

    async def run_observations(
        self, observations: list[SourceObservation], worker: str = "external"
    ) -> IngestReport:
        """Ingest observations fetched *elsewhere* — the Node scraper worker's entry point.

        The connector set is career-ops' providers, which run in their native runtime in
        `services/scraper-node` (ARCHITECTURE.md D4). They fetch on their side and post envelopes
        here, which land in the same dedup + upsert path everything else does. Nothing downstream
        can tell where a job came from by runtime — one corpus, one dedup.

        Rate limiting and circuit breaking are deliberately *not* applied here. Those govern
        outbound requests, and by the time observations reach this method the requests are already
        done; the worker owns its own pacing. Snapshotting is likewise the worker's job, since it
        is the only side that saw the raw payload.

        Derived-field extraction *is* applied here. The corpus has no in-process adapters any more
        (`galaxy/sources/` is deleted), so this is the one place seniority and years-of-experience
        get derived. Skipping it would silently break seniority filtering rather than erroring:
        retrieval's hard filter treats a NULL seniority as "don't exclude", so `max_seniority`
        degrades into a no-op and every senior role sails through a junior-only search.
        """
        report = IngestReport()
        by_site: dict[str, int] = {}
        for o in observations:
            by_site[str(o.site)] = by_site.get(str(o.site), 0) + 1
        for site, count in by_site.items():
            metrics.record_run(site, ok=True, jobs=count)

        # Fills only what is still None, so a worker that already derived a field keeps its value.
        for o in observations:
            apply_derived_fields(o.fields)

        log.info("ingest.observations_received", worker=worker, count=len(observations), sites=len(by_site))
        return await self._finalize(list(observations), report)
