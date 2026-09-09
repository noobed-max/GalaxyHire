"""Unit tests for the search brain — planner, RRF, ranker, eval metrics (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from galaxy.models.enums import Seniority
from galaxy.models.profile import Profile, Project, Skill
from galaxy.search.embedder import HashingEmbedder, embedding_text
from galaxy.search.eval import Judgment, ndcg_at_k, recall_at_k, summarize_judgments
from galaxy.search.planner import compile_query, slice_for_role
from galaxy.search.ranker import rank, to_dict
from galaxy.search.retrieval import _hard_filters, _location_parts, rrf_fuse
from galaxy.search.roles import lexical_query, title_matches

# --- planner ---------------------------------------------------------------


def test_planner_detects_seniority_and_years_negative():
    c = compile_query(
        search_term="senior backend engineer",
        negative_phrases=["5+ years"],
        max_seniority="mid",
    )
    assert c.positive.seniority == Seniority.SENIOR
    assert c.negative.min_years_above == 5  # lifted from the "5+ years" phrase
    assert c.negative.seniority_above == Seniority.MID


def test_profile_slice_picks_role_tagged_projects():
    profile = Profile(
        user_id=uuid4(),
        skills=[Skill(name="Python", tags=["swe"]), Skill(name="Leadership", tags=["manager"])],
        projects=[
            Project(title="API platform", skills=["Python"], role_tags=["swe"]),
            Project(title="Team of 8", skills=["Leadership"], role_tags=["manager"]),
        ],
    )
    swe = slice_for_role(profile, ["swe"])
    assert [p.title for p in swe.projects] == ["API platform"]
    assert "Python" in swe.skill_names and "Leadership" not in swe.skill_names


def test_software_role_family_is_a_hard_title_filter():
    compiled = compile_query(search_term="software developer")
    where, params = _hard_filters(compiled, "hashing-v1")
    assert "lower(title) ~ ANY(:role_title_patterns)" in where
    assert params["role_title_patterns"]


def test_title_negatives_do_not_search_the_whole_description():
    compiled = compile_query(
        search_term="software engineer",
        negative_titles=["senior", "staff"],
        negative_phrases=["security clearance"],
    )
    where, params = _hard_filters(compiled, "hashing-v1")
    assert "to_tsvector('english', coalesce(title, ''))" in where
    assert params["neg_title_q"] == "senior OR staff"
    assert params["neg_q"] == "security clearance"


def test_software_aliases_share_one_lexical_query_and_title_family():
    expanded = lexical_query("SDE")
    assert '"software engineer"' in expanded
    assert '"software developer"' in expanded
    assert title_matches("software engineer", "Backend Developer")
    assert title_matches("software developer", "SDE I")
    assert not title_matches("SDE", "Production Associate")


def test_location_components_and_bengaluru_aliases_match_across_fields():
    compiled = compile_query(search_term="software engineer", location="Bangalore, India")
    where, params = _hard_filters(compiled, "hashing-v1")
    assert "concat_ws" in where
    assert "ILIKE ANY(:loc_0)" in where
    assert "ILIKE ANY(:loc_1)" in where
    assert params["loc_0"] == ["%bengaluru%", "%bangalore%"]
    assert params["loc_1"] == ["%india%"]


def test_located_search_does_not_blanket_admit_remote():
    # A "REMOTE (EMEA/APAC)" posting is not a UK job. Remote rows must name the place like
    # everyone else (bare-"Remote" only passes a remote search) — the old blanket bypass
    # filled every located search with remote-anywhere rows.
    compiled = compile_query(search_term="software engineer", location="United Kingdom")
    where, _ = _hard_filters(compiled, "hashing-v1")
    assert "IS TRUE" not in where


def test_country_shorthands_expand_instead_of_substring_matching():
    # A bare %uk% would match inside unrelated words, so short tokens match by word-boundary
    # regex carrying the raw token plus expansions; the ILIKE arm keeps the long forms.
    # Must agree with the scraper's ingest gate (relevance.ts normalizedLocation).
    like, pattern = _location_parts("London, UK")[1]
    assert "%united kingdom%" in like and "%uk%" not in like
    assert "uk" in pattern and "britain" in pattern  # raw token + expansions, word-boundaried
    # GB and UK are the same country in both directions (gate and clause must agree).
    assert "gb" in _location_parts("UK")[0][1]
    assert "uk" in _location_parts("GB")[0][1]
    # Full names resolve to the same set as their shorthands.
    assert _location_parts("United Kingdom") == _location_parts("UK")
    assert _location_parts("England")[0][0] == _location_parts("UK")[0][0]
    like_usa, pattern_usa = _location_parts("USA")[0]
    assert like_usa == ["%usa%", "%united states%", "%united states of america%"]
    assert "us" in pattern_usa  # short forms ride the word-boundary regex, never ILIKE
    assert _location_parts("Germany") == [(["%germany%"], r"\y(?:germany)\y")]


# --- RRF -------------------------------------------------------------------


def test_rrf_rewards_agreement_across_legs():
    lexical = ["a", "b", "c"]
    semantic = ["b", "a", "d"]
    skill = ["b", "e"]
    fused = rrf_fuse([lexical, semantic, skill])
    # 'b' appears in all three legs, top of two → wins; 'a' (two legs) beats singletons
    assert fused[0] == "b"
    assert fused[1] == "a"
    assert fused.index("a") < fused.index("c")  # 'a' (2 legs) ranks above 'c' (1 leg)


# --- embedder --------------------------------------------------------------


def test_hashing_embedder_deterministic_and_normalized():
    e = HashingEmbedder(dim=384)
    v1 = e.embed(["python kubernetes engineer"])[0]
    v2 = e.embed(["python kubernetes engineer"])[0]
    assert v1 == v2  # deterministic
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-6  # L2 normalized

    # related text is closer than unrelated
    def cos(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True))

    base = e.embed(["python backend developer"])[0]
    near = e.embed(["python backend engineer"])[0]
    far = e.embed(["marketing brand manager"])[0]
    assert cos(base, near) > cos(base, far)


def test_embedding_text_includes_title_and_keywords():
    t = embedding_text("Engineer", "Build things", ["python", "go"])
    assert "Engineer" in t and "python" in t


# --- ranker ----------------------------------------------------------------


def _row(jid, title, cos, kws, seniority=None, date=None, legit="legit", skills=None):
    return {
        "canonical_job_id": jid,
        "title": title,
        "company": "Acme",
        "primary_url": f"https://x/{jid}",
        "location": {"remote": True},
        "compensation": None,
        "seniority": seniority,
        "min_years_experience": None,
        "onsite_policy": "remote",
        "clearance_required": None,
        "jd_keywords": kws,
        # skill overlap now reads jd_skills (curated), not the keyword bag (docs/04 §3.1);
        # the fixtures' kws are already real skills, so default jd_skills to them.
        "jd_skills": kws if skills is None else skills,
        "legitimacy": {"verdict": legit},
        "date_posted": date,
        "cos_sim": cos,
        "source_count": 1,
    }


def test_ranker_orders_by_fit_and_explains():
    compiled = compile_query(search_term="python engineer", positive_skills=["python", "aws"])
    now = datetime(2026, 7, 23, tzinfo=UTC)
    row_map = {
        "hi": _row("hi", "Python Engineer", 0.9, ["python", "aws"], "senior", now),
        "lo": _row("lo", "Java Engineer", 0.1, ["java"], "senior", now),
    }
    ranked = rank(compiled, ["hi", "lo"], row_map, profile=None, now=now)
    assert ranked[0].canonical_job_id == "hi"
    assert set(ranked[0].explanation.matched_skills) == {"python", "aws"}
    assert "java" not in ranked[0].explanation.matched_skills
    assert ranked[0].explanation.fit_band in {"strong", "good"}
    # normalized breakdown sums to the fit score (before penalties; legit=legit → no penalty)
    assert abs(sum(ranked[0].explanation.score_breakdown.values()) - ranked[0].fit_score) < 1e-3


def test_ranker_carries_site_through_to_dict():
    # Provider identity must survive the search boundary: without it every lead
    # fell back to platform="corpus" and the pipeline lost its per-source view.
    compiled = compile_query(search_term="python engineer", positive_skills=["python"])
    now = datetime(2026, 7, 23, tzinfo=UTC)
    row = _row("s1", "Python Engineer", 0.9, ["python"], "senior", now)
    row["site"] = "greenhouse"
    ranked = rank(compiled, ["s1"], {"s1": row}, profile=None, now=now)
    assert ranked[0].site == "greenhouse"
    assert to_dict(ranked[0])["site"] == "greenhouse"


def test_ranker_site_defaults_to_none_when_absent():
    compiled = compile_query(search_term="python engineer", positive_skills=["python"])
    now = datetime(2026, 7, 23, tzinfo=UTC)
    row = _row("s2", "Python Engineer", 0.9, ["python"], "senior", now)  # no site key
    ranked = rank(compiled, ["s2"], {"s2": row}, profile=None, now=now)
    assert ranked[0].site is None


def test_ranker_badges_only_source_proved_freshness():
    # 2026-09 ruling: every match is served with its date shown, so the badge answers only
    # "is this posting fresh" — dated inside 24h earns "<24h"; older dates and undated rows
    # carry no badge (the card shows the date itself instead of an invented claim).
    from datetime import timedelta

    now = datetime(2026, 7, 23, tzinfo=UTC)
    fresh = _row("f", "Engineer", 0.5, ["python"], "mid", now - timedelta(hours=6))
    undated = _row("u", "Engineer", 0.5, ["python"], "mid", None)
    stale = _row("s", "Engineer", 0.5, ["python"], "mid", now - timedelta(days=9))
    for compiled in (
        compile_query(search_term="engineer", positive_skills=["python"], fresh_hours=24),
        compile_query(search_term="engineer", positive_skills=["python"]),
    ):
        ranked = rank(compiled, ["f", "u", "s"], {"f": fresh, "u": undated, "s": stale}, None, now)
        by_id = {j.canonical_job_id: j for j in ranked}
        assert by_id["f"].freshness == "posted"
        assert by_id["u"].freshness is None
        assert by_id["s"].freshness is None
    assert to_dict(by_id["f"])["freshness"] == "posted"


def test_ranker_sort_recent_is_latest_first():
    from datetime import timedelta

    compiled = compile_query(search_term="engineer", positive_skills=["python"])
    now = datetime(2026, 7, 23, tzinfo=UTC)
    old = now - timedelta(days=20)
    new = now - timedelta(days=1)
    # 'old' has the BETTER fit but 'new' is more recent
    row_map = {
        "old": _row("old", "Python Engineer", 0.9, ["python"], "mid", old),
        "new": _row("new", "Python Engineer", 0.2, ["python"], "mid", new),
    }
    by_fit = rank(compiled, ["old", "new"], row_map, None, now, sort="relevance")
    assert by_fit[0].canonical_job_id == "old"  # relevance → best fit first
    by_recent = rank(compiled, ["old", "new"], row_map, None, now, sort="recent")
    assert by_recent[0].canonical_job_id == "new"  # recent → latest posting first


def test_ranker_scam_penalty():
    compiled = compile_query(search_term="engineer", positive_skills=["python"])
    now = datetime(2026, 7, 23, tzinfo=UTC)
    clean = {"c": _row("c", "Engineer", 0.8, ["python"], "mid", now, legit="legit")}
    scam = {"s": _row("s", "Engineer", 0.8, ["python"], "mid", now, legit="scam")}
    c_score = rank(compiled, ["c"], clean, None, now)[0].fit_score
    s_score = rank(compiled, ["s"], scam, None, now)[0].fit_score
    assert s_score < c_score  # scam is penalized


def test_skill_overlap_reads_jd_skills_not_keyword_bag():
    # the keyword bag mentions 'python' but the curated jd_skills is empty → NOT a skill match
    compiled = compile_query(search_term="engineer", positive_skills=["python"])
    now = datetime(2026, 7, 23, tzinfo=UTC)
    row = _row("j", "Engineer", 0.5, ["python", "team", "customer"], "mid", now, skills=[])
    ranked = rank(compiled, ["j"], {"j": row}, None, now)
    assert ranked[0].explanation.matched_skills == []
    assert "python" in ranked[0].explanation.missing_skills


def test_skill_overlap_canonicalizes_aliases():
    # profile lists "ReactJS"; the job's jd_skills has canonical "react" → they must match
    compiled = compile_query(search_term="engineer", positive_skills=["ReactJS"])
    now = datetime(2026, 7, 23, tzinfo=UTC)
    row = _row("j", "Engineer", 0.5, ["team"], "mid", now, skills=["react"])
    ranked = rank(compiled, ["j"], {"j": row}, None, now)
    assert ranked[0].explanation.matched_skills == ["react"]


def test_preference_fit_penalizes_below_comp_floor():
    from galaxy.models.profile import Preferences

    profile = Profile(user_id=uuid4(), preferences=Preferences(comp_min=200000))
    compiled = compile_query(search_term="engineer", positive_skills=["python"], profile=profile)
    now = datetime(2026, 7, 23, tzinfo=UTC)
    low = _row("low", "Engineer", 0.5, ["python"], "mid", now)
    low["compensation"] = {"max_amount": 100000}
    high = _row("high", "Engineer", 0.5, ["python"], "mid", now)
    high["compensation"] = {"max_amount": 250000}
    low_pref = rank(compiled, ["low"], {"low": low}, profile, now)[0].explanation.score_breakdown[
        "preference_fit"
    ]
    high_pref = rank(compiled, ["high"], {"high": high}, profile, now)[0].explanation.score_breakdown[
        "preference_fit"
    ]
    assert low_pref < high_pref  # comp below the floor pulls preference_fit down


def test_graph_proof_rewards_evidenced_skills():
    profile = Profile(
        user_id=uuid4(),
        skills=[Skill(name="python", tags=["swe"])],
        projects=[Project(title="ETL", skills=["python"], role_tags=["swe"])],
    )
    compiled = compile_query(search_term="python engineer", positive_skills=["python"], profile=profile)
    now = datetime(2026, 7, 23, tzinfo=UTC)
    row_map = {"j": _row("j", "Python Engineer", 0.8, ["python"], "mid", now)}
    ranked = rank(compiled, ["j"], row_map, profile=profile, now=now)
    assert ranked[0].explanation.proof_projects  # a real project evidenced the skill
    assert ranked[0].explanation.score_breakdown["graph_proof"] > 0


# --- eval metrics ----------------------------------------------------------


def test_ndcg_and_recall():
    rel = {"a": 3, "b": 2, "c": 0, "d": 1}
    # perfect order
    assert ndcg_at_k(["a", "b", "d", "c"], rel, 10) == 1.0
    # worst-ish order scores lower
    assert ndcg_at_k(["c", "d", "b", "a"], rel, 10) < 1.0
    # recall@2 finds 2 of the 3 relevant
    assert recall_at_k(["a", "b"], rel, 2) == 2 / 3


def test_human_judgments_keep_constraint_failures_separate_from_relevance():
    summary = summarize_judgments(
        [
            Judgment("entry-swe", "a", 1, 3),
            Judgment("entry-swe", "b", 2, 1),
            # A topically relevant senior role is still a hard failure for this query.
            Judgment("entry-swe", "c", 3, 2, hard_violation=True),
            Judgment("entry-swe", "d", 4, 0),
        ]
    )
    assert summary.queries == 1
    assert summary.precision_at_10 == 0.75
    assert summary.hard_violation_rate == 0.25
    assert summary.per_query["entry-swe"]["hard_violations"] == 1


def test_human_judgments_reject_invalid_grades():
    with pytest.raises(ValueError, match="0 to 3"):
        summarize_judgments([Judgment("q", "a", 1, 4)])
