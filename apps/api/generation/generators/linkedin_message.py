from __future__ import annotations

from generation.generators.base import GeneratedAsset
from generation.generators.outreach_email import _fallback_outreach


class LinkedInMessageGenerator:
    name = "linkedin_note"

    def generate(self, lead: dict, profile: dict, config: dict | None = None) -> GeneratedAsset:
        return {"type": self.name, "text": _fallback_outreach(profile, lead)["linkedin_note"]}
