# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vasudev Siddh and vasu-devs
"""apply_selection: narrowing a (tag-scoped) profile to the user's hand-picked
skills/entries/points, with stale-id accounting — and the fallback package
honoring those picks instead of its own project ranking."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generation.selection import (  # noqa: E402
    apply_selection,
    build_selection_block,
    is_empty_selection,
    picked_points_by_row,
    split_blob_points,
)
from profile.ingest_parse import point_id  # noqa: E402


def _profile() -> dict:
    return {
        "n": "Test",
        "skills": [
            {"id": "sk1", "n": "Rust", "cat": "language"},
            {"id": "sk2", "n": "SQL", "cat": "database"},
        ],
        "projects": [
            {
                "id": "proj1", "title": "RoQ", "stack": ["Go"],
                "impact": "Built Raft KV store.\nCut latency by 10x.",
                "points": [
                    {"id": point_id("proj1", "Built Raft KV store."), "text": "Built Raft KV store."},
                    {"id": point_id("proj1", "Cut latency by 10x."), "text": "Cut latency by 10x."},
                ],
            },
            {"id": "proj2", "title": "Scraper", "stack": [], "impact": "", "points": []},
        ],
        "exp": [
            {
                "id": "exp1", "role": "Intern", "co": "Acme", "period": "2024",
                "d": "Shipped the ingest pipeline.",
                "points": [{"id": point_id("exp1", "Shipped the ingest pipeline."),
                            "text": "Shipped the ingest pipeline."}],
            },
            {"id": "exp2", "role": "Barista", "co": "Cafe", "period": "2023", "d": "", "points": []},
        ],
    }


def test_split_blob_points_matches_profile_ids():
    parent = f"p-{uuid.uuid4().hex[:6]}"
    blob = "Built X.\nBuilt Y."
    derived = split_blob_points(parent, blob)
    assert [p["text"] for p in derived] == ["Built X.", "Built Y."]
    assert all(p["id"] == point_id(parent, p["text"]) for p in derived)


def test_empty_selection_is_a_no_op():
    profile = _profile()
    for empty in (None, {}, {"skills_on": [], "points_on": []}):
        assert is_empty_selection(empty)
        out, dropped = apply_selection(profile, empty)
        assert out is profile and dropped == 0


def test_selection_filters_skills_entries_and_points():
    profile = _profile()
    p_pick = point_id("proj1", "Cut latency by 10x.")
    e_pick = point_id("exp1", "Shipped the ingest pipeline.")
    selection = {
        "version": 1,
        "sections": ["summary", "skills", "experience", "projects"],
        "experience_order": [],
        "experience_on": ["exp1"],
        "projects_order": ["proj1"],
        "projects_on": ["proj1"],
        "points_on": [p_pick, e_pick],
        "skills_on": ["sk1"],
    }
    scoped, dropped = apply_selection(profile, selection)
    assert dropped == 0
    assert [s["id"] for s in scoped["skills"]] == ["sk1"]
    # rows without any picked point are gone
    assert [r["id"] for r in scoped["projects"]] == ["proj1"]
    assert [r["id"] for r in scoped["exp"]] == ["exp1"]
    # only the picked points survive, verbatim
    assert scoped["projects"][0]["points"] == [{"id": p_pick, "text": "Cut latency by 10x."}]
    # input profile untouched
    assert len(profile["projects"]) == 2 and len(profile["skills"]) == 2


def test_order_arrays_reorder_rows_and_points():
    profile = _profile()
    profile["projects"].append({
        "id": "proj0", "title": "First", "stack": [],
        "impact": "Alpha bullet.", "points": [{"id": point_id("proj0", "Alpha bullet."), "text": "Alpha bullet."}],
    })
    a = point_id("proj1", "Built Raft KV store.")
    b = point_id("proj1", "Cut latency by 10x.")
    selection = {
        "projects_order": ["proj0", "proj1"],
        "projects_on": ["proj0"],  # entry toggle keeps ALL of its points
        "points_on": [b, a],  # reversed relative to the stored order
    }
    scoped, dropped = apply_selection(profile, selection)
    assert dropped == 0
    assert [r["id"] for r in scoped["projects"]] == ["proj0", "proj1"]
    assert [p["id"] for p in scoped["projects"][1]["points"]] == [b, a]


def test_unknown_ids_dropped_silently_but_counted():
    profile = _profile()
    selection = {
        "skills_on": ["ghost-skill", "sk2"],
        "projects_on": ["gone-project"],
        "experience_on": ["nope", "exp2"],
        "points_on": ["deadbeef0000"],
    }
    scoped, dropped = apply_selection(profile, selection)
    assert dropped == 4
    assert scoped["skills"][0]["id"] == "sk2"
    assert scoped["projects"] == []  # nothing matched -> section empties
    assert scoped["exp"][0]["id"] == "exp2"


class _FakePointTags:
    @staticmethod
    def scoped_point_ids(tag_id):
        return {}


class _FakeRepo:
    """Shape-compatible repo stub (mirrors test_point_tags._FakeRepo); tag
    scoping already ran before selections are applied."""
    point_tags = _FakePointTags()


def test_entry_toggle_without_point_picks_keeps_all_points():
    profile = _profile()
    scoped, dropped = apply_selection(profile, {"projects_on": ["proj1"], "skills_on": []}, _FakeRepo())
    assert dropped == 0
    assert len(scoped["projects"]) == 1
    assert len(scoped["projects"][0]["points"]) == 2  # header check = whole entry


def test_build_selection_block_lists_resolved_rows_verbatim():
    profile = _profile()
    e_pick = point_id("exp1", "Shipped the ingest pipeline.")
    scoped, _ = apply_selection(profile, {
        "experience_on": ["exp1"], "points_on": [e_pick], "skills_on": ["sk2"],
    })
    block = build_selection_block(scoped)
    assert "PROJECTS:" not in block  # filtered out entirely
    assert "### Intern - Acme 2024" in block
    assert "- Shipped the ingest pipeline." in block
    assert "SKILLS (include exactly these): SQL" in block


def test_fallback_package_honors_picked_projects_and_points():
    from generation.generators.resume import _fallback_package

    profile = _profile()
    lead = {"title": "Systems Engineer", "company": "Acme", "description": "Raft consensus",
            "reason": "", "match_points": []}
    raft_pick = point_id("proj1", "Built Raft KV store.")
    selection = {"projects_on": ["proj1"], "points_on": [raft_pick], "skills_on": ["sk1"]}
    package = _fallback_package(profile, lead, selection=selection)
    resume = package.resume_markdown
    assert "- Built Raft KV store." in resume          # picked point verbatim
    assert "- Cut latency by 10x." not in resume       # unpicked sibling excluded
    assert "### Scraper" not in resume                 # unpinned row stays out
    assert package.selected_projects == ["RoQ"]
    # no selection -> unchanged default behavior path still works
    plain = _fallback_package(profile, lead)
    assert "## PROJECTS" in plain.resume_markdown
