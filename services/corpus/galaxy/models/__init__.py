"""Shared domain models (docs/03, docs/04)."""

from galaxy.models.enums import (
    ApplicationStatus,
    CompInterval,
    JobStatus,
    Legitimacy,
    OnsitePolicy,
    SalarySource,
    Seniority,
    Site,
    SourceKind,
    source_trust,
)
from galaxy.models.job import (
    CanonicalJob,
    Compensation,
    FieldWithProvenance,
    LegitimacyVerdict,
    Location,
    RawJobFields,
    SourceObservation,
)
from galaxy.models.profile import Identity, Preferences, Profile, Project, Skill

__all__ = [
    "ApplicationStatus",
    "CanonicalJob",
    "CompInterval",
    "Compensation",
    "FieldWithProvenance",
    "Identity",
    "JobStatus",
    "Legitimacy",
    "LegitimacyVerdict",
    "Location",
    "OnsitePolicy",
    "Preferences",
    "Profile",
    "Project",
    "RawJobFields",
    "SalarySource",
    "Seniority",
    "Site",
    "Skill",
    "SourceKind",
    "SourceObservation",
    "source_trust",
]
