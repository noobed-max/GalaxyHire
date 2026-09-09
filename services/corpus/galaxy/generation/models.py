"""Generation data models (docs/05)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SelectedProject:
    project_id: str
    title: str
    bullets: list[str]
    relevance: float
    matched_skills: list[str]


@dataclass
class SelectionResult:
    """Which of the user's REAL projects/skills fit this job (selection, not authorship)."""

    projects: list[SelectedProject] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)  # skill names, relevance-ordered
    omitted: list[dict] = field(default_factory=list)  # {project_id, title, reason}
    experience: list[dict] = field(default_factory=list)  # field-sliced work history (docs/11 §W1.3)
    # Projects `slice_for_role` matched by role_tags, before relevance scoring. Kept so the
    # "never empty-handed" fallback stays inside the role, instead of reaching for every project.
    role_sliced_projects: list = field(default_factory=list)


@dataclass
class ResumeDoc:
    """An assembled, ATS-safe resume built only from profile content."""

    name: str
    contact: dict  # email, phone, links, location
    summary: str
    skills: list[str]
    projects: list[SelectedProject]
    experience: list[dict]
    education: list[dict]
    target_title: str = ""
    target_company: str = ""


@dataclass
class AtsBreakdown:
    """Deterministic ATS score (docs/05 §4), reweighted for our template pipeline."""

    overall: float
    keyword_match: float
    skills_coverage: float
    keyword_in_context: float
    missing_keywords: list[str] = field(default_factory=list)


@dataclass
class LintIssue:
    kind: str  # e.g. "multi_column", "uniform_bullets"
    message: str
    severity: str = "warn"  # warn | fail


@dataclass
class LintResult:
    ats_safety: list[LintIssue] = field(default_factory=list)
    ai_risk: list[LintIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "fail" for i in (*self.ats_safety, *self.ai_risk))
