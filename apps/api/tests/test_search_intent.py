"""Regression coverage for the search brief → scrape phrase → filter boundary."""

from __future__ import annotations

import pytest

from api.routers.discovery import SearchRequest, _compile_constraints, _query_for
from core.search_intent import (
    fallback_search_intent,
    normalize_search_intent,
    role_title_matches,
)

REPORTED_BRIEF = (
    "Software engineer, no senior, no 3+ years of experience, no SDE 2, no SDE 3"
)


def test_reported_brief_sends_only_the_role_to_the_scraper():
    query, filters = normalize_search_intent(REPORTED_BRIEF)

    assert query == "software engineer"
    assert "senior" not in query.casefold()
    assert "sde" not in query.casefold()
    assert "negative_phrases" not in filters
    assert {"senior", "staff", "principal", "lead", "manager", "architect", "SDE 2", "SDE 3"} <= set(
        filters["negative_titles"]
    )
    assert filters["max_seniority"] == "mid"
    assert filters["max_years"] == 2


def test_a_parser_echoing_the_whole_sentence_cannot_pollute_the_scrape_phrase():
    query, filters = normalize_search_intent(
        REPORTED_BRIEF,
        {"search_term": REPORTED_BRIEF},
    )

    assert query == "software engineer"
    assert filters["max_years"] == 2


def test_resume_details_are_never_a_search_fallback():
    profile = {
        "exp": [
            {"role": "Software Engineering Intern", "company": "CIA Labs"},
            {"role": "Intern", "company": "SuprMentr"},
        ],
        "skills": [{"n": "Rust"}, {"n": "GoLang"}],
    }

    assert _query_for(profile, {"job_preferences": REPORTED_BRIEF}, None) == REPORTED_BRIEF
    assert _query_for(profile, {}, None) == ""
    assert _query_for(profile, {}, "Frontend Engineer") == "Frontend Engineer"


@pytest.mark.asyncio
async def test_constraint_compilation_stays_safe_when_the_remote_parser_fails():
    class BrokenCorpus:
        async def parse_query(self, _text):
            raise RuntimeError("parser offline")

    query, filters = await _compile_constraints(
        BrokenCorpus(),
        REPORTED_BRIEF,
        SearchRequest(),
    )

    assert query == "software engineer"
    assert filters["max_seniority"] == "mid"
    assert filters["max_years"] == 2
    assert {"senior", "staff", "SDE 2", "SDE 3"} <= set(filters["negative_titles"])
    assert "negative_phrases" not in filters


def test_plus_years_means_the_threshold_itself_is_excluded():
    intent = fallback_search_intent("backend engineer, no 1+ years of experience")
    assert intent["max_years"] == 0


@pytest.mark.parametrize(
    "typed",
    ["software engineer", "software developer", "SDE", "SWE", "sofwtare engineer"],
)
def test_equivalent_or_misspelled_software_roles_share_one_collection_phrase(typed):
    query, _filters = normalize_search_intent(typed)
    assert query == "software engineer"


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer",
        "Software Developer",
        "Software Development Engineer I",
        "SDE 1",
        "Backend Developer",
        "Full-Stack Engineer",
    ],
)
def test_software_family_accepts_title_synonyms(title):
    assert role_title_matches("software engineer", title) is True


@pytest.mark.parametrize(
    "title",
    ["Production Associate", "Business Development Associate", "Mechanical Design in SolidWorks"],
)
def test_software_family_rejects_the_reported_unrelated_titles(title):
    assert role_title_matches("software engineer", title) is False


def test_places_named_in_the_brief_become_the_location_filter():
    # The reported bug: "Software Engineer, ..., India, Bengaluru" searched the whole
    # world because the places vanished — the role phrase drops them and nothing else
    # set location, so a stale worldwide scrape served as fresh.
    from core.search_intent import extract_location_phrase

    assert extract_location_phrase(
        "Software Engineer, no SDE 2, no SDE 3, no senior, no principal, India, Bengaluru"
    ) == "Bengaluru, India"
    query, filters = normalize_search_intent(
        "Software Engineer, no SDE 2, no SDE 3, no senior, no principal, India, Bengaluru",
        {"search_term": "Software Engineer, , , , , India, Bengaluru"},
    )
    assert query == "software engineer"
    assert filters["location"] == "Bengaluru, India"


@pytest.mark.parametrize(
    ("brief", "expected"),
    [
        ("software engineer London", "London"),
        ("SDE, UK", "United Kingdom"),
        ("data analyst, no senior, New York", "New York"),
        ("software engineer", ""),
        ("Software Engineer, Indiana", ""),  # substring, not a place — must not match
        ("nurse, no night shifts, Texas", "Texas, United States"),
        ("swe, Karnataka", "Karnataka, India"),
        ("backend developer, remote, Germany", "Germany"),
    ],
)
def test_location_extraction_cases(brief, expected):
    from core.search_intent import extract_location_phrase

    assert extract_location_phrase(brief) == expected


@pytest.mark.asyncio
async def test_explicit_empty_location_overrides_inferred_location():
    class DummyCorpus:
        async def parse_query(self, _text):
            return {"location": "London"}

    # When location is explicitly empty string, it clears inferred location
    _query, filters = await _compile_constraints(
        DummyCorpus(),
        "software engineer in London",
        SearchRequest(location=""),
    )
    assert "location" not in filters


@pytest.mark.asyncio
async def test_explicit_provided_location_overrides_inferred_location():
    class DummyCorpus:
        async def parse_query(self, _text):
            return {"location": "London"}

    _query, filters = await _compile_constraints(
        DummyCorpus(),
        "software engineer in London",
        SearchRequest(location="Bengaluru, India"),
    )
    assert filters["location"] == "Bengaluru, India"
