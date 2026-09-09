"""The three-layer job model (docs/03).

RawJobFields  → SourceObservation  → CanonicalJob

RawJobFields is what a source returned (normalized but not merged). SourceObservation is one
per-source sighting. CanonicalJob is the deduplicated, source-merged role with per-field
provenance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from galaxy.models.enums import (
    CompInterval,
    JobStatus,
    Legitimacy,
    OnsitePolicy,
    SalarySource,
    Seniority,
    Site,
)


class Location(BaseModel):
    country: str | None = None
    state: str | None = None
    city: str | None = None
    remote: bool = False


class Compensation(BaseModel):
    interval: CompInterval | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    currency: str | None = None
    salary_source: SalarySource | None = None


class RawJobFields(BaseModel):
    """Normalized-but-not-merged fields from a single source."""

    title: str
    company: str
    location: Location = Field(default_factory=Location)
    description_html: str | None = None
    description_md: str | None = None
    compensation: Compensation | None = None
    date_posted: datetime | None = None
    # derived at ingest by the normalizer (docs/02 §3.1)
    seniority: Seniority | None = None
    min_years_experience: int | None = None
    onsite_policy: OnsitePolicy | None = None
    clearance_required: bool | None = None
    jd_keywords: list[str] = Field(default_factory=list)
    jd_skills: list[str] = Field(default_factory=list)  # curated skills (docs/04 §3.1)
    extra: dict[str, Any] = Field(default_factory=dict)


class SourceObservation(BaseModel):
    """One per-source sighting of a logical role (docs/03 §1.1)."""

    site: Site
    source_job_id: str  # the platform's own id — unique only WITH `site`
    url: str
    observed_at: datetime
    raw_title: str | None = None
    fields: RawJobFields


class FieldWithProvenance[T](BaseModel):
    """A field value that remembers which source it came from (docs/03 §1.2)."""

    value: T
    source: Site
    source_id: str
    observed_at: datetime


class LegitimacyVerdict(BaseModel):
    verdict: Legitimacy = Legitimacy.UNCERTAIN
    signals: list[str] = Field(default_factory=list)


class CanonicalJob(BaseModel):
    """One deduplicated role with per-field provenance (docs/03 §1.2)."""

    canonical_job_id: str
    # flat "winning" values for ergonomic access
    title: str
    company: str
    company_key: str
    location: Location
    description_md: str | None = None
    primary_url: str
    compensation: Compensation | None = None
    # derived-at-ingest fields
    seniority: Seniority | None = None
    min_years_experience: int | None = None
    onsite_policy: OnsitePolicy | None = None
    clearance_required: bool | None = None
    jd_keywords: list[str] = Field(default_factory=list)
    jd_skills: list[str] = Field(default_factory=list)  # curated skills (docs/04 §3.1)
    # provenance + lifecycle
    sources: list[SourceObservation] = Field(default_factory=list)
    fields: dict[str, FieldWithProvenance[Any]] = Field(default_factory=dict)
    status: JobStatus = JobStatus.OPEN
    legitimacy: LegitimacyVerdict = Field(default_factory=LegitimacyVerdict)
    first_seen_at: datetime
    last_seen_at: datetime
    date_posted: datetime | None = None
    normalizer_version: str
    embedding_version: str | None = None
    merged_at: datetime

    @property
    def also_seen_count(self) -> int:
        """How many distinct sources surfaced this role."""
        return len({o.site for o in self.sources})
