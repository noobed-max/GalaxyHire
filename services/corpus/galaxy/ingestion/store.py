"""Job store — persistence for canonical jobs + observations (docs/03 §6).

A Protocol with two implementations: PostgresJobStore (real) and InMemoryJobStore (tests /
dev without a DB). The ingestion pipeline depends only on the Protocol.
"""

from __future__ import annotations

import json
from typing import Protocol

from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker
from galaxy.models.job import CanonicalJob


class JobStore(Protocol):
    async def upsert_jobs(self, jobs: list[CanonicalJob]) -> int: ...
    async def count(self) -> int: ...


def _job_row(job: CanonicalJob) -> dict:
    return {
        "canonical_job_id": job.canonical_job_id,
        "title": job.title,
        "company": job.company,
        "company_key": job.company_key,
        "location": job.location.model_dump_json(),
        "description_md": job.description_md,
        "primary_url": job.primary_url,
        "fields": json.dumps({k: v.model_dump(mode="json") for k, v in job.fields.items()}),
        "compensation": job.compensation.model_dump_json() if job.compensation else None,
        "seniority": job.seniority.value if job.seniority else None,
        "min_years_experience": job.min_years_experience,
        "onsite_policy": job.onsite_policy.value if job.onsite_policy else None,
        "clearance_required": job.clearance_required,
        "jd_keywords": json.dumps(job.jd_keywords),
        "jd_skills": json.dumps(job.jd_skills),
        "status": job.status.value,
        "legitimacy": job.legitimacy.model_dump_json(),
        "first_seen_at": job.first_seen_at,
        "last_seen_at": job.last_seen_at,
        "date_posted": job.date_posted,
        "normalizer_version": job.normalizer_version,
        "embedding_version": job.embedding_version,
    }


_UPSERT_JOB = text(
    """
    INSERT INTO canonical_jobs (
        canonical_job_id, title, company, company_key, location, description_md, primary_url,
        fields, compensation, seniority, min_years_experience, onsite_policy, clearance_required,
        jd_keywords, jd_skills, status, legitimacy, first_seen_at, last_seen_at, date_posted,
        normalizer_version, embedding_version
    ) VALUES (
        :canonical_job_id, :title, :company, :company_key, CAST(:location AS jsonb),
        :description_md, :primary_url, CAST(:fields AS jsonb), CAST(:compensation AS jsonb),
        :seniority, :min_years_experience, :onsite_policy, :clearance_required,
        CAST(:jd_keywords AS jsonb), CAST(:jd_skills AS jsonb), :status, CAST(:legitimacy AS jsonb),
        :first_seen_at, :last_seen_at, :date_posted, :normalizer_version, :embedding_version
    )
    ON CONFLICT (canonical_job_id) DO UPDATE SET
        title = EXCLUDED.title,
        company = EXCLUDED.company,
        company_key = EXCLUDED.company_key,
        location = EXCLUDED.location,
        description_md = EXCLUDED.description_md,
        primary_url = EXCLUDED.primary_url,
        fields = EXCLUDED.fields,
        compensation = EXCLUDED.compensation,
        seniority = EXCLUDED.seniority,
        min_years_experience = EXCLUDED.min_years_experience,
        onsite_policy = EXCLUDED.onsite_policy,
        clearance_required = EXCLUDED.clearance_required,
        jd_keywords = EXCLUDED.jd_keywords,
        jd_skills = EXCLUDED.jd_skills,
        normalizer_version = EXCLUDED.normalizer_version,
        legitimacy = EXCLUDED.legitimacy,
        last_seen_at = GREATEST(canonical_jobs.last_seen_at, EXCLUDED.last_seen_at),
        first_seen_at = LEAST(canonical_jobs.first_seen_at, EXCLUDED.first_seen_at),
        date_posted = COALESCE(EXCLUDED.date_posted, canonical_jobs.date_posted)
    """
)

_UPSERT_OBS = text(
    """
    INSERT INTO source_observations (
        site, source_job_id, canonical_job_id, url, raw_title, raw_fields,
        first_observed_at, last_observed_at
    ) VALUES (
        :site, :source_job_id, :canonical_job_id, :url, :raw_title, CAST(:raw_fields AS jsonb),
        :observed_at, :observed_at
    )
    ON CONFLICT (site, source_job_id) DO UPDATE SET
        canonical_job_id = EXCLUDED.canonical_job_id,
        url = EXCLUDED.url,
        raw_title = EXCLUDED.raw_title,
        raw_fields = EXCLUDED.raw_fields,
        last_observed_at = GREATEST(source_observations.last_observed_at, EXCLUDED.last_observed_at)
    """
)


class PostgresJobStore:
    async def upsert_jobs(self, jobs: list[CanonicalJob]) -> int:
        if not jobs:
            return 0
        sm = get_sessionmaker()
        async with sm() as session:
            async with session.begin():
                for job in jobs:
                    await session.execute(_UPSERT_JOB, _job_row(job))
                    for obs in job.sources:
                        await session.execute(
                            _UPSERT_OBS,
                            {
                                "site": obs.site.value,
                                "source_job_id": obs.source_job_id,
                                "canonical_job_id": job.canonical_job_id,
                                "url": obs.url,
                                "raw_title": obs.raw_title,
                                "raw_fields": obs.fields.model_dump_json(),
                                "observed_at": obs.observed_at,
                            },
                        )
        return len(jobs)

    async def count(self) -> int:
        sm = get_sessionmaker()
        async with sm() as session:
            return (await session.execute(text("SELECT count(*) FROM canonical_jobs"))).scalar() or 0


class InMemoryJobStore:
    """Dict-backed store for tests / DB-less dev."""

    def __init__(self) -> None:
        self.jobs: dict[str, CanonicalJob] = {}

    async def upsert_jobs(self, jobs: list[CanonicalJob]) -> int:
        for job in jobs:
            self.jobs[job.canonical_job_id] = job
        return len(jobs)

    async def count(self) -> int:
        return len(self.jobs)
