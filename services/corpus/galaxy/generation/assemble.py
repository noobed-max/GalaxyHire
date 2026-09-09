"""Assemble a ResumeDoc from selection + profile (docs/05 §3).

Content is placed as-is; nothing is invented. Section order follows an ATS-safe layout. Optional
reframing (opt-in, LLM) runs UPSTREAM in generation.service via reframe.reframe_projects — it
rewords only non-verbatim bullets and every rewrite is vetted by the fact-diff guard
(reframe.safe_reframe) before it ever reaches here, so assembly itself stays pure.
"""

from __future__ import annotations

from galaxy.generation.models import ResumeDoc, SelectedProject, SelectionResult
from galaxy.models.profile import Profile


def assemble(
    selection: SelectionResult,
    profile: Profile,
    job: dict,
    summary: str | None = None,
) -> ResumeDoc:
    contact = {
        "email": profile.identity.email,
        "phone": profile.identity.phone,
        "links": profile.identity.links,
        "location": profile.identity.locations[0] if profile.identity.locations else None,
    }
    # summary defaults to a factual one-liner built from profile roles + top skills (no invention)
    if not summary:
        roles = ", ".join(profile.roles) if profile.roles else "Engineer"
        top_skills = ", ".join(selection.skills[:6])
        summary = f"{roles}. Core skills: {top_skills}." if top_skills else f"{roles}."

    # experience: the field-sliced set from selection (docs/11 §W1.3), falling back to the whole
    # history when nothing was sliced (e.g. an override path that didn't run selection).
    experience_src = selection.experience if selection.experience else profile.experience

    return ResumeDoc(
        name=profile.identity.name or "",
        contact=contact,
        summary=summary,
        skills=selection.skills[:20],
        projects=selection.projects,
        experience=_experience(experience_src),
        education=profile.education,
        target_title=job.get("title", ""),
        target_company=job.get("company", ""),
    )


def _experience(entries: list[dict]) -> list[dict]:
    out: list[dict] = []
    for e in entries:
        if isinstance(e, dict):
            out.append(
                {
                    "title": e.get("title") or e.get("role") or "",
                    "company": e.get("company") or "",
                    "dates": e.get("dates") or "",
                    "bullets": e.get("bullets") or [],
                }
            )
    return out


def empty_projects_fallback(projects: list, limit: int = 4) -> list[SelectedProject]:
    """Surface projects when scoring matched nothing, so a résumé is never empty-handed.

    Takes an explicit project list rather than the whole `Profile`, and that distinction is the
    whole point. Callers pass the **role-sliced** projects — the ones `slice_for_role` already
    matched by `role_tags`.

    Reading `profile.projects` here instead was a real bug: on a job with no description, scoring
    has only the title to work with and legitimately scores everything 0, so this fallback ran and
    threw away a slice that had *correctly* narrowed to the relevant work. A Platform Engineer
    résumé came out containing a warehouse-inventory dashboard — precisely the outcome §5 gives as
    its example of what not to do.
    """
    return [
        SelectedProject(p.id, p.title, list(p.bullets), 0.0, [])
        for p in list(projects)[:limit]
    ]
