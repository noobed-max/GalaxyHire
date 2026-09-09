"""Dedup engine tests — the Phase 1 exit criterion (docs/10).

Proves BOTH:
  - collision case: the same role from two sources → ONE canonical row (with provenance)
  - anti-over-merge: two genuinely distinct same-title reqs → TWO rows
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from galaxy.dedup.canonical import canonical_job_id
from galaxy.dedup.engine import DedupEngine
from galaxy.models.enums import SalarySource, Site
from galaxy.models.job import (
    Compensation,
    Location,
    RawJobFields,
    SourceObservation,
)

NOW = datetime(2026, 7, 23, tzinfo=UTC)


def _obs(site: Site, job_id: str, *, title, company, city="Seattle", country="USA",
         remote=False, desc="", url=None, comp=None, posted=None, observed=NOW):
    fields = RawJobFields(
        title=title,
        company=company,
        location=Location(city=city, country=country, remote=remote),
        description_md=desc,
        compensation=comp,
        date_posted=posted,
    )
    return SourceObservation(
        site=site,
        source_job_id=job_id,
        url=url or f"https://{site.value}.example/{job_id}",
        observed_at=observed,
        raw_title=title,
        fields=fields,
    )


def test_collision_same_role_two_sources_merges_to_one():
    """LinkedIn's copy and the ATS copy of the same job → one CanonicalJob."""
    shared_desc = (
        "We are hiring a Senior Software Engineer to build distributed systems. "
        "You will design services, mentor engineers, and own reliability. "
        "Requires 5+ years of experience with Python and cloud infrastructure."
    )
    obs = [
        _obs(Site.LINKEDIN, "ln-1", title="Senior Software Engineer", company="Amazon", desc=shared_desc),
        _obs(Site.GREENHOUSE, "gh-1", title="Sr. Software Engineer", company="Amazon.com, Inc.",
             desc=shared_desc, comp=Compensation(min_amount=180000, max_amount=220000,
                                                  salary_source=SalarySource.DIRECT)),
    ]
    result = DedupEngine().dedup(obs)

    assert len(result.jobs) == 1, "same role from two sources must collapse to one row"
    job = result.jobs[0]
    assert job.also_seen_count == 2
    assert {o.site for o in job.sources} == {Site.LINKEDIN, Site.GREENHOUSE}
    # per-field provenance: ATS (Greenhouse) is higher trust, wins the apply URL + compensation
    assert job.fields["url"].source == Site.GREENHOUSE
    assert job.compensation is not None
    assert job.fields["compensation"].source == Site.GREENHOUSE
    # both source-local ids retained
    ids = {(o.site, o.source_job_id) for o in job.sources}
    assert (Site.LINKEDIN, "ln-1") in ids and (Site.GREENHOUSE, "gh-1") in ids


def test_over_merge_distinct_reqs_stay_separate():
    """Two distinct SDE II — Seattle reqs (different teams/descriptions) → two rows."""
    obs = [
        _obs(Site.GREENHOUSE, "gh-a", title="Software Development Engineer II", company="Amazon",
             desc="Join the S3 storage team. Work on durability, erasure coding, and low-latency "
                  "object retrieval at exabyte scale. Deep C++ and distributed systems focus."),
        _obs(Site.GREENHOUSE, "gh-b", title="Software Development Engineer II", company="Amazon",
             desc="Join the Alexa NLU team. Build natural-language understanding models, ranking, "
                  "and dialog systems in Python and PyTorch for conversational AI."),
    ]
    result = DedupEngine().dedup(obs)

    assert len(result.jobs) == 2, "distinct reqs with different descriptions must NOT over-merge"
    assert result.stats.base_groups == 1  # same company|title|location
    assert result.stats.split_groups == 1  # the description bucket split it
    assert result.stats.split_rate == 1.0


def test_near_identical_descriptions_still_merge():
    """True duplicates (trivially different descriptions) still collapse."""
    base = "Backend Engineer role building payment APIs with Go and Postgres. " * 5
    obs = [
        _obs(Site.LEVER, "lv-1", title="Backend Engineer", company="Stripe", desc=base + "Apply now."),
        _obs(Site.INDEED, "in-1", title="Backend Engineer", company="Stripe", desc=base + "Apply today."),
    ]
    result = DedupEngine().dedup(obs)
    assert len(result.jobs) == 1


def test_date_posted_takes_earliest_credible():
    early = NOW - timedelta(days=5)
    late = NOW - timedelta(days=1)
    desc = "Data Engineer building pipelines with Spark and Airflow. " * 6
    obs = [
        _obs(Site.INDEED, "in-2", title="Data Engineer", company="Netflix", desc=desc, posted=late),
        _obs(Site.LEVER, "lv-2", title="Data Engineer", company="Netflix", desc=desc, posted=early),
    ]
    job = DedupEngine().dedup(obs).jobs[0]
    assert job.date_posted == early


def test_canonical_id_is_stable_across_runs():
    f = RawJobFields(title="Sr. SWE", company="Amazon, Inc.",
                     location=Location(city="Seattle", country="USA"), description_md="hello world")
    assert canonical_job_id(f) == canonical_job_id(f)
