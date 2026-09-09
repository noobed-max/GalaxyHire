"""Generation unit tests — selection, ATS scoring, reframe guard, lint, renderers (no DB)."""

from __future__ import annotations

from uuid import uuid4

from galaxy.generation.assemble import assemble
from galaxy.generation.ats_score import score
from galaxy.generation.cover import cover_letter
from galaxy.generation.models import ResumeDoc, SelectedProject
from galaxy.generation.reframe import (
    lint,
    reframe_is_faithful,
    reframe_projects,
    safe_reframe,
)
from galaxy.generation.render import to_docx, to_html, to_markdown, to_pdf
from galaxy.generation.select import apply_overrides, select
from galaxy.models.profile import Identity, Profile, Project, Skill

JOB = {
    "canonical_job_id": "j1",
    "title": "Senior Python Engineer",
    "company": "Acme",
    "jd_keywords": ["python", "django", "postgres", "aws", "kubernetes"],
}


def _profile() -> Profile:
    return Profile(
        user_id=uuid4(),
        identity=Identity(name="Ada Dev", email="ada@example.com", links=["github.com/ada"]),
        roles=["swe"],
        skills=[
            Skill(name="Python", tags=["swe"]),
            Skill(name="Django", tags=["swe"]),
            Skill(name="Photography", tags=["hobby"]),
        ],
        projects=[
            Project(title="Django REST API",
                    bullets=["Built a Django API on Postgres serving 2M requests/day."],
                    skills=["Python", "Django", "Postgres"], role_tags=["swe"]),
            Project(title="Wedding photos", bullets=["Shot 30 weddings."],
                    skills=["Photography"], role_tags=["hobby"]),
        ],
    )


# --- selection -------------------------------------------------------------


def test_select_picks_relevant_projects_only():
    sel = select(JOB, _profile())
    titles = [p.title for p in sel.projects]
    assert "Django REST API" in titles
    assert "Wedding photos" not in titles  # no overlap → omitted
    assert any(o["title"] == "Wedding photos" for o in sel.omitted)
    # matched skills come only from the profile, never invented
    matched = sel.projects[0].matched_skills
    assert set(matched) <= {"Python", "Django", "Postgres"}


def test_select_never_invents_skills():
    sel = select(JOB, _profile())
    # 'kubernetes'/'aws' are in the JD but NOT the profile → must not appear in skills
    assert "Kubernetes" not in sel.skills and "AWS" not in sel.skills
    assert "Photography" in sel.skills  # profile skill, even if low relevance, is retained


def test_override_pin_and_drop():
    p = _profile()
    sel = select(JOB, p)
    photo_id = next(pr.id for pr in p.projects if pr.title == "Wedding photos")
    django_id = next(pr.id for pr in p.projects if pr.title == "Django REST API")
    updated = apply_overrides(sel, p, pin=[photo_id], drop=[django_id])
    ids = {pr.project_id for pr in updated.projects}
    assert photo_id in ids and django_id not in ids  # user override wins


# --- ATS score -------------------------------------------------------------


def test_ats_score_rewards_keyword_in_context():
    p = _profile()
    doc = assemble(select(JOB, p), p, JOB)
    ats = score(doc, JOB)
    assert 0.0 < ats.overall <= 1.0
    # python/django appear in a project bullet → keyword_in_context > 0
    assert ats.keyword_in_context > 0
    # aws/kubernetes are unmatched → listed as missing
    assert "aws" in ats.missing_keywords or "kubernetes" in ats.missing_keywords


# --- reframe fact-guard ----------------------------------------------------


def test_reframe_guard_rejects_invented_facts():
    original = "Built a Django API serving 2M requests/day."
    faithful = "Engineered a Django API handling 2M requests/day."  # no new facts
    injected = "Built a Django API serving 50M requests/day at Google."  # new number + org
    assert reframe_is_faithful(original, faithful)
    assert not reframe_is_faithful(original, injected)
    assert safe_reframe(original, injected) == original  # rejected → keep original
    assert safe_reframe(original, faithful) == faithful


async def test_reframe_projects_applies_faithful_and_rejects_injected():
    # a fake reframer: proposes a faithful reword for the first bullet, an injected one for the
    # second. The wiring must apply the guard to each and never touch a verbatim project.
    proposals = {
        "Built a Django API serving 2M requests/day.":
            "Engineered a Django API serving 2M requests/day.",  # faithful → applied
        "Owned the payments service.":
            "Owned the payments service at Google.",  # new org → rejected
    }

    async def fake_reframer(bullet: str, keywords: list[str]) -> str:
        return proposals.get(bullet, bullet)

    editable = SelectedProject("p1", "API", list(proposals.keys()), 1.0, [])
    verbatim = SelectedProject("p2", "Locked", ["Kept exactly as written."], 1.0, [])

    out = await reframe_projects([editable, verbatim], {"p2"}, ["django"], fake_reframer)

    assert out[0].bullets[0] == "Engineered a Django API serving 2M requests/day."  # reworded
    assert out[0].bullets[1] == "Owned the payments service."  # guard rejected the injection
    assert out[1].bullets == ["Kept exactly as written."]  # verbatim project untouched


async def test_reframe_projects_survives_reframer_errors():
    async def boom(bullet: str, keywords: list[str]) -> str:
        raise RuntimeError("llm down")

    proj = SelectedProject("p1", "API", ["Original bullet."], 1.0, [])
    out = await reframe_projects([proj], set(), [], boom)
    assert out[0].bullets == ["Original bullet."]  # error → keep original, never crash


def test_lint_flags_ai_tells():
    doc = ResumeDoc(
        name="X", contact={"email": "x@y.z"}, summary="Passionate about leveraging cutting-edge synergy.",
        skills=["Python"], projects=[], experience=[], education=[],
    )
    res = lint(doc)
    assert any(i.kind == "llm_phrasing" for i in res.ai_risk)


# --- renderers -------------------------------------------------------------


def test_renderers_produce_output():
    p = _profile()
    doc = assemble(select(JOB, p), p, JOB)
    md = to_markdown(doc)
    assert "Django REST API" in md and "Ada Dev" in md
    assert "ada@example.com" in md  # contact in the body (ATS-safe)

    html = to_html(doc)
    assert "<h1>Ada Dev</h1>" in html and "single column" or "Django" in html

    docx_bytes = to_docx(doc)
    assert docx_bytes[:2] == b"PK"  # docx is a zip

    pdf_bytes = to_pdf(doc)
    assert pdf_bytes[:5] == b"%PDF-"  # valid PDF header


def test_cover_letter_grounded_in_profile():
    p = _profile()
    sel = select(JOB, p)
    letter = cover_letter(p, JOB, sel)
    assert "Acme" in letter and "Senior Python Engineer" in letter
    assert "Ada Dev" in letter  # signed with the real name
