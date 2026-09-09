from __future__ import annotations

from generation.generators.base import GeneratedAsset


def _fallback_outreach(profile: dict, lead: dict) -> dict:
    """Generate deterministic outreach messages when LLM fails or returns empty."""
    name = profile.get("n") or "Candidate"
    title = lead.get("title", "the role")
    company = lead.get("company", "your company")
    skills = [s.get("n", "") for s in profile.get("skills", []) if s.get("n")]
    top_skills = ", ".join(skills[:4]) if skills else "software engineering"

    founder_message = (
        f"Your {title} role caught my eye — the problem space is compelling.\n"
        f"I bring hands-on {top_skills} experience with shipped projects that map to your stack.\n"
        f"Happy to share specifics or jump on a quick call."
    )
    linkedin_note = (
        f"Hi! Saw the {title} opening at {company}. "
        f"My background in {', '.join(skills[:3]) if skills else 'full-stack development'} "
        f"maps well to the role. Would love to connect and share more."
    )
    cold_email = (
        f"Subject: {title} at {company} — relevant {top_skills} background\n\n"
        f"Hi {company} team,\n\n"
        f"I came across the {title} role and it aligns closely with my work in {top_skills}. "
        f"I have shipped production systems that mirror the requirements in your posting. "
        f"I would welcome the chance to share specific project examples that demonstrate direct fit.\n\n"
        f"Best regards,\n{name}"
    )
    return {
        "founder_message": founder_message[:280],
        "linkedin_note": linkedin_note[:300],
        "cold_email": cold_email[:600],
    }


class OutreachEmailGenerator:
    name = "cold_email"

    def generate(self, lead: dict, profile: dict, config: dict | None = None) -> GeneratedAsset:
        return {"type": self.name, "text": _fallback_outreach(profile, lead)["cold_email"]}
