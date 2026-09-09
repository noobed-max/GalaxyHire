"""Normalizer + derived-field extractor tests (docs/02 §3.1, docs/04 §6).

The negative-filter fixture suite lives here — this is where negative-search correctness
actually lives (docs/04 §6).
"""

from __future__ import annotations

import pytest

from galaxy.ingestion.normalize import (
    canonical_skill,
    extract_clearance_required,
    extract_jd_keywords,
    extract_jd_skills,
    extract_min_years,
    extract_onsite_policy,
    extract_seniority,
    norm_company,
    norm_location,
    norm_title,
)
from galaxy.models.enums import OnsitePolicy, Seniority


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Amazon.com, Inc.", "amazon"),
        ("Amazon", "amazon"),
        ("The Acme Corporation", "acme"),
        ("Café Corp GmbH", "cafe"),
        ("Stripe, Inc.", "stripe"),
    ],
)
def test_norm_company_collapses_variants(raw, expected):
    assert norm_company(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Sr. Software Engineer", "senior software engineer"),
        ("Senior Software Engineer", "senior software engineer"),
        ("Sr. SWE (Remote) #12345", "senior software engineer"),
        ("Jr. Developer", "junior developer"),
    ],
)
def test_norm_title_folds_synonyms_and_noise(raw, expected):
    assert norm_title(raw) == expected


def test_norm_location_remote_sentinel():
    assert norm_location("Seattle", "USA", remote=True) == "remote"
    assert norm_location("Seattle", "USA", remote=False) == "seattle usa"


# --- years-of-experience negative-filter fixtures (docs/04 §6) --------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Requires 3+ years of experience", 3),
        ("2-4 yrs experience required", 2),
        ("at least three years of experience", 3),
        ("minimum of 5 years experience", 5),
        ("36 months of experience required", 3),
        ("We value collaboration", None),  # unstated → None, never excluded
        ("Founded 3 years ago", None),  # not an experience context
    ],
)
def test_extract_min_years(text, expected):
    assert extract_min_years(text) == expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Software Engineer Intern", Seniority.INTERN),
        ("Senior Backend Engineer", Seniority.SENIOR),
        ("Staff Software Engineer", Seniority.LEAD),
        ("VP of Engineering", Seniority.EXEC),
        ("Junior Data Analyst", Seniority.JUNIOR),
    ],
)
def test_extract_seniority(title, expected):
    assert extract_seniority(title) == expected


def test_extract_onsite_policy():
    assert extract_onsite_policy("This is a fully remote role", False) == OnsitePolicy.REMOTE
    assert extract_onsite_policy("Hybrid, 3 days in office", False) == OnsitePolicy.HYBRID
    assert extract_onsite_policy("On-site in Austin", False) == OnsitePolicy.ONSITE


def test_extract_clearance():
    assert extract_clearance_required("Active TS/SCI clearance required") is True
    assert extract_clearance_required("Must be a US citizen") is True
    assert extract_clearance_required("Great benefits") is None


def test_extract_jd_keywords_dedups_and_ranks():
    kws = extract_jd_keywords("Python Engineer", "Python Python Django REST APIs and Python")
    assert "python" in kws
    assert kws.count("python") == 1  # deduped
    assert kws[0] == "python"  # most frequent first


# --- jd_skills: curated skill extraction vs the keyword bag (docs/04 §3.1) --


def test_extract_jd_skills_picks_real_skills_not_filler():
    skills = extract_jd_skills(
        "Senior Backend Engineer",
        "Build services in Python and Go. Experience with Kubernetes, PostgreSQL, and React.js. "
        "Strong REST API design. Our team values collaboration and a customer focus.",
    )
    # real technologies are captured (react.js folded to its canonical form)
    assert {"python", "go", "kubernetes", "postgresql", "react", "rest"} <= set(skills)
    # JD filler that the keyword bag WOULD surface must never be treated as a skill
    for filler in ("team", "customer", "collaboration", "experience", "values"):
        assert filler not in skills


def test_extract_jd_skills_ignores_word_boundaries_and_folds_multiword():
    # "go" must not match inside "google"; "google cloud" folds to gcp
    assert extract_jd_skills("Eng", "We deploy on Google Cloud") == ["gcp"]
    assert set(extract_jd_skills("Eng", "Machine learning with PyTorch and CI/CD")) == {
        "machine learning", "pytorch", "ci/cd"
    }


def test_extract_jd_skills_empty_for_no_skills():
    assert extract_jd_skills("Manager", "Own strategy and lead the team to success.") == []


def test_canonical_skill_folds_aliases_and_passes_unknown_through():
    assert canonical_skill("ReactJS") == "react"
    assert canonical_skill("golang") == "go"
    assert canonical_skill("K8s") == "kubernetes"
    assert canonical_skill("Machine Learning") == "machine learning"
    assert canonical_skill("SomeNicheFramework") == "somenicheframework"
