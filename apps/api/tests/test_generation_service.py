import asyncio
from types import SimpleNamespace
from unittest import mock

from generation.service import GenerationService


def _fake_repo():
    return SimpleNamespace(
        settings=SimpleNamespace(get_settings=lambda: {"contact_lookup_enabled": "true"}),
        profile=SimpleNamespace(get_profile=lambda: {"n": "Candidate"}),
    )


def test_generation_service_generates_package():
    service = GenerationService(repo=_fake_repo())
    expected = {"resume": "resume.pdf", "cover_letter": "cover.pdf"}

    with mock.patch("generation.service.run_package", return_value=expected) as run_package:
        result = asyncio.run(service.generate_package({"title": "Role"}, "template"))

    assert result == expected
    run_package.assert_called_once_with(
        {"title": "Role"}, "template", service.repo, "", "",
        selection=None, contact_kinds=None, show_location=None,
    )


def test_generation_service_looks_up_contacts():
    service = GenerationService(repo=_fake_repo())
    expected = {"contacts": [{"email": "a@example.com"}]}

    with mock.patch("generation.service.lookup_contacts", return_value=expected) as lookup:
        result = asyncio.run(service.lookup_contact({"company": "Acme"}))

    assert result == expected
    lookup.assert_called_once_with({"company": "Acme"}, {"contact_lookup_enabled": "true"}, {"n": "Candidate"})


def test_generation_service_can_generate_with_contacts_disabled():
    service = GenerationService()
    package = {"resume": "resume.pdf"}

    with mock.patch.object(service, "generate_package", return_value=package) as generate, \
         mock.patch.object(service, "lookup_contact") as lookup:
        result = asyncio.run(service.generate_with_contacts({"title": "Role"}, include_contacts=False))

    assert result.package == package
    assert result.contact_lookup is None
    generate.assert_called_once()
    lookup.assert_not_called()


def test_generation_service_keeps_package_when_contact_lookup_fails():
    service = GenerationService()
    package = {"resume": "resume.pdf", "cover_letter": "cover.pdf"}

    with mock.patch.object(service, "generate_package", return_value=package), \
         mock.patch.object(service, "lookup_contact", side_effect=RuntimeError("hunter unavailable")):
        result = asyncio.run(service.generate_with_contacts({"job_id": "job-1", "title": "Role"}))

    assert result.package == package
    assert result.contact_lookup == {"contacts": [], "error": "hunter unavailable"}
