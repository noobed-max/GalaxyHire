from __future__ import annotations

import asyncio
from dataclasses import dataclass

from data.repository import Repository, create_repository
from core.logging import get_logger


_log = get_logger(__name__)


@dataclass
class GenerationResult:
    package: dict
    contact_lookup: dict | None = None


def run_package(lead: dict, template: str = "", repo: Repository | None = None, cover_letter_base: str = "",
                tag_id: str = "", selection: dict | None = None, contact_kinds: list[str] | None = None,
                show_location: bool | None = None) -> dict:
    from generation.generator import run_package as _run_package

    return _run_package(lead, template, repo=repo, cover_letter_base=cover_letter_base, tag_id=tag_id,
                        selection=selection, contact_kinds=contact_kinds, show_location=show_location)


def lookup_contacts(lead: dict, settings: dict | None = None, profile: dict | None = None) -> dict:
    from generation.contact_lookup import run as _lookup_contacts

    return _lookup_contacts(lead, settings=settings, profile=profile)


class GenerationService:
    def __init__(self, repo: Repository | None = None):
        self.repo = repo or create_repository()

    async def generate_package(self, lead: dict, template: str = "", cover_letter_base: str = "",
                               tag_id: str = "", selection: dict | None = None,
                               contact_kinds: list[str] | None = None,
                               show_location: bool | None = None) -> dict:
        return await asyncio.to_thread(
            run_package, lead, template, self.repo, cover_letter_base, tag_id,
            selection=selection, contact_kinds=contact_kinds, show_location=show_location,
        )

    async def lookup_contact(self, lead: dict) -> dict:
        settings = await asyncio.to_thread(self.repo.settings.get_settings)
        profile = await asyncio.to_thread(self.repo.profile.get_profile)
        return await asyncio.to_thread(lookup_contacts, lead, settings, profile)

    async def suggest_selection(self, lead: dict, tag_id: str = ""):
        """AI pick + ATS suggestions over the tag-scoped profile (no drafting).

        Scoping lives here (not the router) because only the generation package may
        read profile internals — see test_import_boundaries.
        """
        from generation.generators.package import _drop_conflict_duplicates, _scope_profile_to_tag
        from generation.suggest import suggest_selection as _suggest

        profile = await asyncio.to_thread(self.repo.profile.get_profile)
        if tag_id:
            profile = await asyncio.to_thread(_scope_profile_to_tag, profile, tag_id, self.repo)
        try:
            profile = await asyncio.to_thread(_drop_conflict_duplicates, profile, self.repo)
        except Exception as exc:
            _log.warning("conflict narrowing skipped for suggest: %s", exc)
        return await asyncio.to_thread(_suggest, profile, lead, tag_id)

    async def generate_with_contacts(        self,
        lead: dict,
        *,
        template: str = "",
        include_contacts: bool = True,
        cover_letter_base: str = "",
        tag_id: str = "",
        selection: dict | None = None,
        contact_kinds: list[str] | None = None,
        show_location: bool | None = None,
    ) -> GenerationResult:
        package = await self.generate_package(lead, template, cover_letter_base, tag_id, selection,
                                              contact_kinds, show_location)
        contacts = None
        if include_contacts:
            try:
                contacts = await self.lookup_contact(lead)
            except Exception as exc:
                _log.warning("contact lookup skipped for %s: %s", lead.get("job_id", "?"), exc)
                contacts = {"contacts": [], "error": str(exc)}
        return GenerationResult(package=package, contact_lookup=contacts)


def create_generation_service() -> GenerationService:
    return GenerationService()
