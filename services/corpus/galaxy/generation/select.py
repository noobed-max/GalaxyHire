"""Project/skill selection (docs/05 §2) — the core, LLM-as-selector.

The model's job is to SELECT which of the user's real projects/skills best fit a job, never to
author content. This module is the deterministic selector (skill-overlap + graph-proof), always
available; an LLM selector can refine the shortlist later behind the same interface. Nothing is
ever invented — a project the user didn't enter cannot appear; a skill not in the profile cannot
be added.
"""

from __future__ import annotations

from galaxy.generation.models import SelectedProject, SelectionResult
from galaxy.models.profile import Profile
from galaxy.search.planner import slice_for_role

MAX_PROJECTS = 4


def _job_terms(job: dict) -> set[str]:
    kws = {k.lower() for k in (job.get("jd_keywords") or [])}
    title = (job.get("title") or "").lower()
    return kws | set(title.split())


def select(job: dict, profile: Profile, max_projects: int = MAX_PROJECTS) -> SelectionResult:
    """Deterministically pick the most relevant real projects/skills for this job.

    Relevance = overlap between a project's skills and the job's keyword set. Ties broken by
    number of matched skills, then original order (stable).
    """
    role_terms = [job.get("title", ""), *(job.get("jd_keywords") or [])[:10]]
    prof_slice = slice_for_role(profile, role_terms)
    job_terms = _job_terms(job)

    scored: list[tuple[float, list[str], object]] = []
    for project in prof_slice.projects:
        pskills = [s for s in project.skills if s.lower() in job_terms]
        # also credit keywords that literally appear in the bullets
        bullet_blob = " ".join(project.bullets).lower()
        bullet_hits = {t for t in job_terms if len(t) > 2 and t in bullet_blob}
        relevance = len(pskills) + 0.5 * len(bullet_hits)
        scored.append((relevance, pskills, project))

    scored.sort(key=lambda t: (t[0], len(t[1])), reverse=True)

    projects: list[SelectedProject] = []
    omitted: list[dict] = []
    for relevance, pskills, project in scored:
        if len(projects) < max_projects and relevance > 0:
            projects.append(
                SelectedProject(
                    project_id=project.id,
                    title=project.title,
                    bullets=list(project.bullets),
                    relevance=round(relevance, 2),
                    matched_skills=pskills,
                )
            )
        else:
            reason = "no overlap with the job" if relevance == 0 else "lower relevance than selected"
            omitted.append({"project_id": project.id, "title": project.title, "reason": reason})

    # skills: profile skills that the job asks for, relevance-ordered, then remaining profile skills
    matched_skills = [s.name for s in prof_slice.skills if s.name.lower() in job_terms]
    other_skills = [s.name for s in prof_slice.skills if s.name not in matched_skills]
    skills = matched_skills + other_skills

    return SelectionResult(
        projects=projects,
        skills=skills,
        omitted=omitted,
        experience=prof_slice.experience,
        # Carried so a caller falling back to "surface something" uses the role-relevant subset
        # rather than re-reading the unfiltered profile.
        role_sliced_projects=list(prof_slice.projects),
    )


def apply_overrides(
    selection: SelectionResult, profile: Profile, pin: list[str], drop: list[str]
) -> SelectionResult:
    """User overrides win (docs/05 §2): pin a project in, drop one out."""
    dropped = set(drop)
    kept = [p for p in selection.projects if p.project_id not in dropped]
    kept_ids = {p.project_id for p in kept}

    by_id = {p.id: p for p in profile.projects}
    for pid in pin:
        if pid in kept_ids or pid not in by_id:
            continue
        proj = by_id[pid]
        kept.append(
            SelectedProject(
                project_id=proj.id,
                title=proj.title,
                bullets=list(proj.bullets),
                relevance=0.0,
                matched_skills=[],
            )
        )
    return SelectionResult(projects=kept, skills=selection.skills, omitted=selection.omitted)
