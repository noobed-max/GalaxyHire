"""Cover letter (docs/05 §7, §5).

The cover letter is the ONE generated artifact, so it defaults to a short factual template
assembled from profile facts + the selected projects (minimal free generation), grounded only in
profile content. An optional LLM pass can polish voice (conditioned on a user writing sample) —
but the deterministic template always produces a valid letter.
"""

from __future__ import annotations

from galaxy.generation.models import SelectionResult
from galaxy.models.profile import Profile


def cover_letter(profile: Profile, job: dict, selection: SelectionResult) -> str:
    name = profile.identity.name or ""
    company = job.get("company", "the team")
    title = job.get("title", "the role")
    top_projects = ", ".join(p.title for p in selection.projects[:2])
    top_skills = ", ".join(selection.skills[:5])

    para1 = (
        f"I'm writing to apply for {title} at {company}. "
        f"My background centers on {top_skills}." if top_skills
        else f"I'm writing to apply for {title} at {company}."
    )
    para2 = (
        f"Relevant work includes {top_projects}, which map directly to what this role calls for."
        if top_projects
        else "My experience maps directly to what this role calls for."
    )
    para3 = "I'd welcome the chance to discuss how I can contribute. Thank you for your consideration."

    signoff = f"\n\nBest regards,\n{name}" if name else "\n\nBest regards,"
    return "\n\n".join([para1, para2, para3]) + signoff
