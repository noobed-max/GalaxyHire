"""Phase W1 unit tests (docs/11): NL search parse, resume extraction/mapping, experience slice."""

from __future__ import annotations

import json
from uuid import uuid4

from galaxy.models.enums import Seniority
from galaxy.models.profile import Profile, Project, Skill
from galaxy.search.nl_parse import _fallback, parse_search
from galaxy.search.planner import compile_query, slice_for_role
from galaxy.search.profile_import import _to_profile, extract_resume_text, import_from_text

CANONICAL = (
    "looking for software engineering roles for intern as well as junior roles, "
    "and I'm not looking for any senior roles or any SDE 2 SDE 3"
)


class _FakeLLM:
    """Returns a fixed tool-call, mimicking the OpenAI-compatible chat response shape."""

    def __init__(self, tool_name: str, args: dict):
        self._name = tool_name
        self._args = args

    async def chat(self, messages, **kwargs):
        return {
            "choices": [
                {"message": {"tool_calls": [
                    {"function": {"name": self._name, "arguments": json.dumps(self._args)}}
                ]}}
            ]
        }


# --- NL search parse --------------------------------------------------------


async def test_parse_search_llm_path_compiles_canonical():
    fake = _FakeLLM("compile_search", {
        "search_term": "software engineer",
        "max_seniority": "junior",
        "negative_titles": ["senior", "sde 2", "sde ii", "sde 3", "sde iii"],
    })
    out = await parse_search(CANONICAL, client=fake)
    assert out["search_term"] == "software engineer"
    assert out["max_seniority"] == "junior"
    assert "senior" in out["negative_titles"] and "sde 3" in out["negative_titles"]


async def test_parse_search_falls_back_when_llm_raises():
    class Boom:
        async def chat(self, *a, **k):
            raise RuntimeError("llm down")

    out = await parse_search(CANONICAL, client=Boom())
    assert "software" in out["search_term"].lower()
    assert out.get("negative_phrases")  # negations lifted deterministically
    assert out.get("max_seniority") == "mid"  # "no senior" → ceiling one band below


def test_fallback_lifts_negation_and_seniority_ceiling():
    out = _fallback("data analyst but not senior")
    assert "data analyst" in out["search_term"].lower()
    assert out["max_seniority"] == "mid"


def test_fallback_keeps_exclusions_out_of_the_role_and_lifts_plus_years():
    out = _fallback(
        "Software engineer, no senior, no 3+ years of experience, no SDE 2, no SDE 3"
    )
    assert out["search_term"] == "Software engineer"
    assert out["negative_phrases"] == [
        "senior",
        "3+ years of experience",
        "SDE 2",
        "SDE 3",
    ]
    assert out["max_seniority"] == "mid"
    assert out["max_years"] == 2


def test_compiled_query_enforces_seniority_ceiling_and_exclusions():
    # what the UI submits after parse → planner turns it into real hard-filter intent
    c = compile_query(
        search_term="software engineer", max_seniority="junior",
        negative_titles=["senior", "sde 3"],
    )
    assert c.negative.seniority_above == Seniority.JUNIOR   # excludes anything more senior
    assert "sde 3" in c.negative.titles


# --- resume extraction + mapping --------------------------------------------


def test_extract_resume_text_txt_and_pdf():
    assert "hello resume" in extract_resume_text("r.txt", b"hello resume")
    # build a real one-page PDF and read it back through pypdf
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "Jane Engineer Python")
    text = extract_resume_text("r.pdf", bytes(pdf.output()))
    assert "Jane Engineer" in text


def test_to_profile_maps_extended_schema():
    data = {
        "name": "Ada Dev", "email": "ada@x.io", "skills": ["Python", "SQL"],
        "projects": [{"title": "ETL", "bullets": ["Built it"], "skills": ["Python"], "role_tags": ["data"]}],
        "experience": [
            {"title": "SWE", "company": "Acme", "dates": "2020-2023",
             "bullets": ["Shipped features"], "tags": ["software"]},
            {"title": "Nurse", "company": "Hospital", "tags": ["nursing"]},
        ],
        "education": [{"institution": "MIT", "degree": "BS CS", "dates": "2016-2020"}],
        "certifications": ["AWS SA"], "anything_else": "Authorized to work in the US.",
    }
    p = _to_profile(uuid4(), data)
    assert p.identity.name == "Ada Dev"
    assert p.projects[0].role_tags == ["data"]
    assert {e["tags"][0] for e in p.experience} == {"software", "nursing"}
    assert p.education[0]["school"] == "MIT"  # institution mapped to the renderer's key
    assert p.certifications == [{"name": "AWS SA"}]
    assert "Authorized" in p.anything_else


async def test_import_from_text_uses_extended_schema():
    fake = _FakeLLM("extract_profile", {
        "name": "Bob", "experience": [{"title": "Accountant", "company": "Firm", "tags": ["finance"]}],
        "anything_else": "2-week notice",
    })
    p = await import_from_text(uuid4(), "resume text", client=fake)
    assert p.identity.name == "Bob"
    assert p.experience[0]["tags"] == ["finance"]
    assert p.anything_else == "2-week notice"


# --- experience slicing (multi-field person) --------------------------------


def _multi_field_profile() -> Profile:
    return Profile(
        user_id=uuid4(),
        skills=[Skill(name="Python", tags=["software"]), Skill(name="Auditing", tags=["finance"])],
        projects=[
            Project(title="API", skills=["Python"], role_tags=["software"]),
            Project(title="Audit", skills=["Auditing"], role_tags=["finance"]),
        ],
        experience=[
            {"title": "SWE", "company": "Acme", "bullets": ["code"], "tags": ["software"]},
            {"title": "Auditor", "company": "Bank", "bullets": ["audit"], "tags": ["finance"]},
        ],
    )


def test_slice_for_role_filters_experience_by_field():
    sl = slice_for_role(_multi_field_profile(), ["software"])
    assert [e["title"] for e in sl.experience] == ["SWE"]  # only the software role
    assert [p.title for p in sl.projects] == ["API"]


def test_slice_for_role_falls_back_to_all_experience_when_untagged():
    sl = slice_for_role(_multi_field_profile(), ["marketing"])  # matches nothing
    assert len(sl.experience) == 2  # fall back to full history, never empty
