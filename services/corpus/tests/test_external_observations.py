"""External-observation ingest — the Node scraper worker's entry point (ARCHITECTURE.md D4/D6).

Two things are under test:

  * `Site` is an *open* enum, so a career-ops provider id the Node worker posts (the vendored
    tree ships ~80) round-trips through the model layer without a Python member existing for it.
  * `IngestPipeline.run_observations` puts externally-fetched observations through the same dedup
    and upsert path as in-process adapters, so the corpus cannot tell which runtime fetched what.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from galaxy.ingestion.pipeline import IngestPipeline
from galaxy.ingestion.store import InMemoryJobStore
from galaxy.models.enums import SOURCE_TRUST, Seniority, Site, source_trust
from galaxy.models.job import Location, RawJobFields, SourceObservation

NOW = datetime(2026, 7, 25, tzinfo=UTC)
SHARED_DESC = (
    "Senior Platform Engineer building Kubernetes tooling and CI/CD. "
    "Requires 6+ years of experience with Go and distributed systems. " * 2
)


def _obs(
    site: str, jid: str, *, title: str = "Senior Platform Engineer", company: str = "Acme"
) -> SourceObservation:
    return SourceObservation(
        site=Site(site),
        source_job_id=jid,
        url=f"https://{site}.example/{jid}",
        observed_at=NOW,
        raw_title=title,
        fields=RawJobFields(
            title=title,
            company=company,
            location=Location(city="Berlin", country="Germany"),
            description_md=SHARED_DESC,
        ),
    )


class TestOpenSiteEnum:
    def test_declared_members_are_untouched(self):
        assert Site.GREENHOUSE == "greenhouse"
        assert Site("greenhouse") is Site.GREENHOUSE

    @pytest.mark.parametrize("site_id", ["jobsoid", "ats-jazzhr", "zip_recruiter", "4dayweek"])
    def test_admits_registry_shaped_ids(self, site_id: str):
        assert Site(site_id) == site_id

    def test_coined_members_have_stable_identity(self):
        # The merge resolver and dedup compare sites; two calls returning distinct objects for
        # the same id would split one portal into two.
        assert Site("jobsoid") is Site("jobsoid")

    def test_case_variants_converge_instead_of_splitting(self):
        assert Site("GREENHOUSE") is Site.GREENHOUSE
        assert Site("JobSoid") is Site("jobsoid")
        assert Site("MiXeD").value == "mixed"

    @pytest.mark.parametrize(
        "bad", ["", "   ", "has space", "-leading", "_under", "semi;colon", "a/b", "x" * 65, 42, None]
    )
    def test_rejects_malformed_ids(self, bad):
        # A worker sending garbage is a bug. Coining a member for it would bury that bug in the
        # corpus under a site name nobody can trace.
        with pytest.raises(ValueError):
            Site(bad)

    def test_long_tail_sites_are_least_trusted_for_field_merges(self):
        # An unvetted long-tail board must not overwrite Greenhouse's view of a job.
        assert source_trust(Site("jobsoid")) < min(SOURCE_TRUST.values())

    def test_observation_model_accepts_an_open_site(self):
        obs = _obs("jobsoid", "js-1")
        assert obs.site == "jobsoid"
        # Round-trips through JSON, which is how it arrives from the Node worker.
        assert SourceObservation.model_validate(obs.model_dump(mode="json")).site == "jobsoid"


class TestRunObservations:
    @pytest.mark.asyncio
    async def test_ingests_a_batch_from_an_unknown_site(self):
        store = InMemoryJobStore()
        report = await IngestPipeline(store).run_observations(
            [_obs("jobsoid", "js-1"), _obs("ats-jazzhr", "jz-9", company="Globex")],
            worker="scraper-node",
        )
        assert report.fetched == 2
        assert report.upserted == 2
        assert report.failures == []

    @pytest.mark.asyncio
    async def test_dedups_the_same_role_seen_by_two_workers_connectors(self):
        # The whole reason the Node worker posts here rather than to its own store: one job
        # observed via two portals must collapse to one canonical row.
        store = InMemoryJobStore()
        report = await IngestPipeline(store).run_observations(
            [_obs("jobsoid", "js-1"), _obs("ats-jazzhr", "jz-1")]
        )
        assert report.fetched == 2
        assert report.dedup is not None
        assert report.dedup.canonical_out == 1, "identical role from two sources should merge"
        assert report.upserted == 1

    @pytest.mark.asyncio
    async def test_distinct_roles_stay_distinct(self):
        store = InMemoryJobStore()
        report = await IngestPipeline(store).run_observations(
            [
                _obs("jobsoid", "js-1", title="Senior Platform Engineer"),
                _obs("jobsoid", "js-2", title="Staff Data Scientist", company="Initech"),
            ]
        )
        assert report.dedup is not None
        assert report.dedup.canonical_out == 2

    @pytest.mark.asyncio
    async def test_derives_the_fields_retrieval_filters_on(self):
        """External observations must get `apply_derived_fields`.

        The Node worker has no adapter to do the deriving, so `run_observations` is the only
        place seniority and min-years get set. Omitting it fails silently rather than loudly:
        retrieval's hard filter treats a NULL seniority as "don't exclude", so `max_seniority`
        quietly becomes a no-op and senior roles pass a junior-only search. This test is the
        guard for that.
        """
        obs = SourceObservation(
            site=Site("jobsoid"),
            source_job_id="js-1",
            url="https://jobsoid.example/1",
            observed_at=NOW,
            fields=RawJobFields(
                title="Senior Platform Engineer",
                company="Acme",
                location=Location(city="Berlin", country="Germany"),
                description_md="We need 6+ years of experience with Go. This role is fully remote.",
            ),
        )
        assert obs.fields.seniority is None, "precondition: the worker sent nothing derived"

        await IngestPipeline(InMemoryJobStore()).run_observations([obs])

        assert obs.fields.seniority == Seniority.SENIOR
        assert obs.fields.min_years_experience == 6
        assert obs.fields.jd_keywords, "keywords feed the ranker and the ATS scorer"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_fields_the_worker_already_derived(self):
        obs = SourceObservation(
            site=Site("jobsoid"),
            source_job_id="js-2",
            url="https://jobsoid.example/2",
            observed_at=NOW,
            fields=RawJobFields(
                title="Senior Platform Engineer",
                company="Acme",
                seniority=Seniority.JUNIOR,  # the source said so explicitly; trust it over the title
                min_years_experience=1,
                description_md="6+ years preferred.",
            ),
        )
        await IngestPipeline(InMemoryJobStore()).run_observations([obs])
        assert obs.fields.seniority == Seniority.JUNIOR
        assert obs.fields.min_years_experience == 1

    @pytest.mark.asyncio
    async def test_an_empty_batch_is_not_an_error(self):
        # A connector legitimately returning nothing this window must not look like a failure.
        report = await IngestPipeline(InMemoryJobStore()).run_observations([])
        assert report.fetched == 0
        assert report.upserted == 0
        assert report.failures == []

    @pytest.mark.asyncio
    async def test_external_and_in_process_observations_dedup_against_each_other(self):
        """Two workers seeing one role must still merge.

        This is the claim D4 rests on — that routing by runtime is invisible to the corpus.
        """
        store = InMemoryJobStore()
        pipeline = IngestPipeline(store)
        first = await pipeline.run_observations([_obs("greenhouse", "gh-1")])
        assert first.upserted == 1
        second = await pipeline.run_observations([_obs("jobsoid", "js-1")])
        # Same title/company/description → the resolver should recognise the existing role.
        assert second.dedup is not None
        assert second.fetched == 1
