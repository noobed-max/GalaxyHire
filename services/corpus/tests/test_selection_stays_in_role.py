"""Project selection must stay inside the role (§5).

§5's own example: "for a software role, pull in the software projects and relevant skills; leave out
unrelated data-management projects." This is the regression guard for a bug that produced exactly
the forbidden outcome — a Platform Engineer résumé containing a warehouse-inventory dashboard.

The mechanism is worth remembering, because nothing in it looked wrong in isolation:

  1. `slice_for_role` matched `role_tags` and *correctly* narrowed to the platform project.
  2. The job had no description (17% of the corpus doesn't), so it carried only two keywords and no
     skills — relevance scoring had nothing to work with and legitimately scored everything 0.
  3. With no project scoring above zero, a "never empty-handed" fallback ran and re-read
     `profile.projects`, throwing the slice away and re-adding the excluded work.

Each step was defensible; the composition was not.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from galaxy.generation.assemble import empty_projects_fallback
from galaxy.generation.select import select
from galaxy.models.profile import Profile, Project, Skill


def profile() -> Profile:
    return Profile(
        user_id=uuid4(),
        skills=[
            Skill(name="Go", tags=["platform"]),
            Skill(name="Kubernetes", tags=["platform"]),
            Skill(name="React", tags=["frontend"]),
        ],
        projects=[
            Project(
                title="Kubernetes autoscaling operator",
                bullets=["Built a controller in Go that scales stateful workloads"],
                skills=["Go", "Kubernetes"],
                role_tags=["platform"],
            ),
            Project(
                title="Warehouse inventory dashboard",
                bullets=["React dashboard for stock reconciliation"],
                skills=["React"],
                role_tags=["data-management"],
            ),
        ],
    )


def job(*, title: str, keywords: list[str] | None = None, description: str | None = None) -> dict:
    return {
        "canonical_job_id": "j1",
        "title": title,
        "jd_keywords": keywords or [],
        "jd_skills": [],
        "description_md": description,
    }


class TestKeywordlessJobs:
    """The hard case: no description, so scoring has only the title."""

    def test_role_slice_excludes_the_unrelated_project(self):
        # Nothing scores above zero here, so what matters is which projects were even considered.
        result = select(job(title="Platform Engineer", keywords=["platform", "engineer"]), profile())
        titles = [p.title for p in result.role_sliced_projects]
        assert "Kubernetes autoscaling operator" in titles
        assert "Warehouse inventory dashboard" not in titles

    def test_the_fallback_stays_inside_the_role(self):
        # This is the actual regression: the fallback must consume the slice, not the whole profile.
        result = select(job(title="Platform Engineer", keywords=["platform", "engineer"]), profile())
        fallback = empty_projects_fallback(result.role_sliced_projects)
        assert [p.title for p in fallback] == ["Kubernetes autoscaling operator"]

    def test_the_old_behaviour_would_have_failed_this(self):
        # Passing the unfiltered project list back reproduces the bug, which is what makes the
        # `role_sliced_projects` argument load-bearing rather than decorative.
        titles = [p.title for p in empty_projects_fallback(profile().projects)]
        assert "Warehouse inventory dashboard" in titles


class TestScoredSelection:
    """With a real description, relevance scoring does the work and should agree."""

    def test_picks_the_matching_project_when_keywords_exist(self):
        result = select(
            job(
                title="Platform Engineer",
                keywords=["platform", "kubernetes", "go", "terraform"],
                description="Run Kubernetes at scale with Go.",
            ),
            profile(),
        )
        assert [p.title for p in result.projects] == ["Kubernetes autoscaling operator"]

    def test_records_why_a_project_was_left_out(self):
        # The user should be able to see that an omission was deliberate.
        result = select(
            job(title="Platform Engineer", keywords=["platform", "kubernetes", "go"]),
            profile(),
        )
        assert all("Warehouse" not in p.title for p in result.projects)

    def test_a_frontend_role_selects_the_other_project(self):
        # Symmetry check: the rule is "stay in role", not "always prefer the platform project".
        result = select(
            job(title="Frontend Engineer", keywords=["frontend", "react"], description="React work."),
            profile(),
        )
        titles = [p.title for p in result.projects] or [p.title for p in result.role_sliced_projects]
        assert "Warehouse inventory dashboard" in titles
        assert "Kubernetes autoscaling operator" not in titles


class TestNothingIsInvented:
    """§5's other half: the model selects, it never authors."""

    def test_selected_bullets_are_verbatim_from_the_profile(self):
        p = profile()
        result = select(job(title="Platform Engineer", keywords=["platform", "kubernetes", "go"]), p)
        original = {b for proj in p.projects for b in proj.bullets}
        for selected in result.projects:
            for bullet in selected.bullets:
                assert bullet in original, "a bullet appeared that the user never wrote"

    def test_no_project_appears_that_the_user_did_not_enter(self):
        p = profile()
        result = select(job(title="Platform Engineer", keywords=["platform"]), p)
        known = {proj.title for proj in p.projects}
        for selected in [*result.projects, *result.role_sliced_projects]:
            assert selected.title in known


@pytest.mark.parametrize("limit", [1, 2, 4])
def test_fallback_respects_its_limit(limit: int):
    projects = profile().projects
    assert len(empty_projects_fallback(projects, limit=limit)) <= limit
