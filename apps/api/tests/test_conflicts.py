# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vasudev Siddh and vasu-devs
"""Tests for duplicate/conflict handling of profile points: the sqlite store
(union-merge, resolve/keep_both/dismiss/reopen, cleanup), the pure near-dupe
detector (stage 0/1/2, over-merge guard, cross-kind guard), and the
generation-time one-wording-per-group enforcement."""

import subprocess
import sys
import uuid
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(__file__).resolve().parent / ".scratch-conflicts"


def _run(body: str) -> None:
    SCRATCH.mkdir(exist_ok=True)
    db_path = str(SCRATCH / f"conflicts-{uuid.uuid4().hex}.db")
    script = (
        "import sys; sys.path.insert(0, '.');"
        "from data.sqlite import conflicts as cf;"
        "from data.sqlite.connection import init_sql;"
        f"db = {db_path!r};"
        "init_sql(db);"
        + body
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"subprocess failed:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}"
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(db_path + suffix)
            if candidate.exists():
                try:
                    candidate.unlink()
                except OSError:
                    pass


# ── store ─────────────────────────────────────────────────────────────────────

def test_conflict_store_roundtrip_and_resolution_states():
    _run(
        "g = cf.create_group('exact', 1.0, [('experience', 'e1'), ('experience', 'e2')], db_path=db)\n"
        "assert g['status'] == 'open' and g['reason'] == 'exact', g\n"
        "groups = cf.list_groups(db_path=db)\n"
        "assert len(groups) == 1 and {m['point_id'] for m in groups[0]['members']} == {'e1', 'e2'}, groups\n"
        "# pick one wording: status pins it\n"
        "r = cf.resolve(g['id'], 'experience', 'e2', db_path=db)\n"
        "assert r['status'] == 'picked' and r['picked_id'] == 'e2', r\n"
        "# keep_both clears the pick but keeps the group constraining nothing\n"
        "r = cf.keep_both(g['id'], db_path=db)\n"
        "assert r['status'] == 'keep_both' and r['picked_id'] is None, r\n"
        "# dismiss / reopen roundtrip\n"
        "r = cf.dismiss(g['id'], db_path=db)\n"
        "assert r['status'] == 'dismissed', r\n"
        "r = cf.reopen(g['id'], db_path=db)\n"
        "assert r['status'] == 'open' and r['picked_kind'] is None, r\n"
        "# a point outside the group cannot be picked into it\n"
        "try:\n"
        "    cf.resolve(g['id'], 'project', 'zzz', db_path=db)\n"
        "    raise SystemExit('expected ValueError')\n"
        "except ValueError:\n"
        "    pass\n"
        "# unknown group id\n"
        "try:\n"
        "    cf.keep_both('nope', db_path=db)\n"
        "    raise SystemExit('expected ValueError')\n"
        "except ValueError:\n"
        "    pass\n"
    )


def test_conflict_store_single_membership_union_merges_groups():
    _run(
        "g1 = cf.create_group('fuzzy', 0.8, [('project', 'p1'), ('project', 'p2')], db_path=db)\n"
        "# p2 already belongs to g1: creating g2 must union-merge, never double-book\n"
        "g2 = cf.create_group('embedding', 0.9, [('project', 'p2'), ('project', 'p3')], db_path=db)\n"
        "groups = cf.list_groups(db_path=db)\n"
        "assert len(groups) == 1, [g['id'] for g in groups]\n"
        "assert {m['point_id'] for m in groups[0]['members']} == {'p1', 'p2', 'p3'}, groups\n"
        "assert groups[0]['id'] == g2['id'] and groups[0]['reason'] == 'embedding'\n"
        "# a lone member cannot form a group\n"
        "try:\n"
        "    cf.create_group('manual', 1.0, [('skill', 's1')], db_path=db)\n"
        "    raise SystemExit('expected ValueError')\n"
        "except ValueError:\n"
        "    pass\n"
        "# cross-kind members are allowed in the store (UI/manual), kinds validated\n"
        "g3 = cf.create_group('manual', 1.0, [('skill', 's1'), ('skill', 's2')], db_path=db)\n"
        "assert {m['point_kind'] for m in g3['members']} == {'skill'}, g3\n"
    )


def test_conflict_store_delete_for_point_and_prune_stale():
    _run(
        "g1 = cf.create_group('exact', 1.0, [('experience', 'e1'), ('experience', 'e2')], db_path=db)\n"
        "g2 = cf.create_group('fuzzy', 0.9, [('project', 'p1'), ('project', 'p2'), ('project', 'p3')], db_path=db)\n"
        "# removing a member of a 2-group empties it below minimum -> gone\n"
        "cf.delete_for_point('experience', 'e1', db_path=db)\n"
        "ids = {g['id'] for g in cf.list_groups(db_path=db)}\n"
        "assert g1['id'] not in ids, ids\n"
        "# removing a member of a 3-group keeps the group alive with 2 members\n"
        "cf.delete_for_point('project', 'p1', db_path=db)\n"
        "kept = {g['id']: {m['point_id'] for m in g['members']} for g in cf.list_groups(db_path=db)}\n"
        "assert kept[g2['id']] == {'p2', 'p3'}, kept\n"
        "# prune_stale drops memberships whose points vanished (p3) + emptied groups\n"
        "summary = cf.prune_stale({('project', 'p2'), ('experience', 'e9')}, db_path=db)\n"
        "assert summary['members_removed'] >= 1, summary\n"
        "assert all(g['id'] != g2['id'] for g in cf.list_groups(db_path=db)), summary\n"
        "# valid pairs survive untouched\n"
        "cf.create_group('exact', 1.0, [('experience', 'x1'), ('experience', 'x2')], db_path=db)\n"
        "before = cf.list_groups(db_path=db)\n"
        "summary = cf.prune_stale({('experience', 'x1'), ('experience', 'x2')}, db_path=db)\n"
        "assert summary['members_removed'] == 0 and len(cf.list_groups(db_path=db)) == len(before)\n"
    )


def test_rescan_detection_is_idempotent_at_store_level():
    # sync_detection twice over unchanged data: the second run must create
    # nothing because every member already carries a membership.
    _run(
        "from data.conflicts_detect import sync_detection\n"
        "units = [\n"
        "    {'kind': 'experience', 'id': 'p1', 'parent_id': 'job-1', 'text': 'Owned PLC integration.', 'header': 'Controls Engineer @ RoboDyne Labs'},\n"
        "    {'kind': 'experience', 'id': 'p2', 'parent_id': 'job-1', 'text': 'Owned PLC integration.', 'header': 'Controls Engineer @ RoboDyne Labs (2021-2025)'},\n"
        "]\n"
        "first = sync_detection(units, cf, db_path=db)\n"
        "assert first == {'groups_detected': 1, 'groups_created': 1}, first\n"
        "second = sync_detection(units, cf, db_path=db)\n"
        "assert second['groups_created'] == 0, second\n"
        "assert len(cf.list_groups(db_path=db)) == 1\n"
        "# dismissed groups are NOT resurrected by a rescan\n"
        "gid = cf.list_groups(db_path=db)[0]['id']\n"
        "cf.dismiss(gid, db_path=db)\n"
        "third = sync_detection(units, cf, db_path=db)\n"
        "assert third['groups_created'] == 0, third\n"
        "assert cf.list_groups(db_path=db)[0]['status'] == 'dismissed'\n"
    )


def test_sync_prunes_legacy_entity_memberships_without_touching_bullets():
    """Old entity-row conflicts converge away during sync; profile points remain candidates."""
    _run(
        "from data.conflicts_detect import sync_detection\n"
        "cf.create_group('exact', 1.0, [('experience', 'old-e1'), ('experience', 'old-e2')], db_path=db)\n"
        "units = [\n"
        "    {'kind': 'experience', 'id': 'bullet-1', 'parent_id': 'job-1', 'text': 'Built payment API.', 'header': 'Engineer Acme'},\n"
        "    {'kind': 'experience', 'id': 'bullet-2', 'parent_id': 'job-1', 'text': 'Built payment API.', 'header': 'Engineer Acme'},\n"
        "]\n"
        "summary = sync_detection(units, cf, db_path=db)\n"
        "assert summary['groups_created'] == 1, summary\n"
        "groups = cf.list_groups(db_path=db)\n"
        "assert len(groups) == 1\n"
        "assert {m['point_id'] for m in groups[0]['members']} == {'bullet-1', 'bullet-2'}\n"
    )


def test_prune_clears_selection_when_picked_entity_membership_vanishes():
    _run(
        "g = cf.create_group('exact', 1.0, [('experience', 'entity-old'), ('experience', 'bullet-1'), ('experience', 'bullet-2')], db_path=db)\n"
        "cf.resolve(g['id'], 'experience', 'entity-old', db_path=db)\n"
        "cf.prune_stale({('experience', 'bullet-1'), ('experience', 'bullet-2')}, db_path=db)\n"
        "out = cf.list_groups(db_path=db)[0]\n"
        "assert out['status'] == 'open' and out['picked_id'] is None\n"
        "assert {m['point_id'] for m in out['members']} == {'bullet-1', 'bullet-2'}\n"
    )


# ── pure detector (in-process, fake embed_fn — never real ONNX) ──────────────

def test_detector_links_robodyne_date_variant_without_models():
    from data.conflicts_detect import detect, loose_key

    assert loose_key("Controls Engineer @ RoboDyne Labs") == loose_key("Controls Engineer @ RoboDyne Labs (2021-2025)")
    pts = [
        {"kind": "experience", "id": "p1", "parent_id": "job-1", "text": "Owned PLC integration.", "header": "Controls Engineer @ RoboDyne Labs"},
        {"kind": "experience", "id": "p2", "parent_id": "job-1", "text": "Owned PLC integration.", "header": "Controls Engineer | RoboDyne Labs (2021-2025)"},
    ]
    groups = detect(pts)
    assert len(groups) == 1, groups
    assert groups[0]["reason"] == "exact" and groups[0]["score"] == 1.0
    assert {(m["kind"], m["id"]) for m in groups[0]["members"]} == {("experience", "p1"), ("experience", "p2")}


def test_detector_fuzzy_jaccard_and_embedding_stages_with_fake_embedder():
    from data.conflicts_detect import EMBED_GRAY_LOW, detect

    # Stage 1: token-set Jaccard >= 0.80 on bullet tokens within one parent.
    pts = [
        {"kind": "project", "id": "p1", "parent_id": "project-1", "text": "Built Realtime Chat System", "header": "Realtime Chat"},
        {"kind": "project", "id": "p2", "parent_id": "project-1", "text": "Built realtime chat messaging system", "header": "Realtime Chat"},
        {"kind": "project", "id": "p3", "parent_id": "project-1", "text": "Totally Different Thing Entirely", "header": "Realtime Chat"},
    ]
    groups = detect(pts)
    assert len(groups) == 1 and groups[0]["reason"] == "fuzzy", groups

    # Stage 2: linked IFF the (fake) embedder places them close together.
    def fake_embed(texts):
        return [[1.0, 0.0, 0.0] if "feed" in t else [0.0, 1.0, 0.0] for t in texts]

    close = [
        {"kind": "experience", "id": "x1", "parent_id": "job-1", "text": "Built feed fan-out in Go", "header": "Role A"},
        {"kind": "experience", "id": "x2", "parent_id": "job-1", "text": "Developed async feed system with Kafka", "header": "Role A"},
    ]
    far = [
        {"kind": "experience", "id": "x1", "parent_id": "job-1", "text": "Nursed patients back to health", "header": "Role A"},
        {"kind": "experience", "id": "x2", "parent_id": "job-1", "text": "Developed async feed system with Kafka", "header": "Role A"},
    ]
    linked = detect(close, embed_fn=fake_embed)
    assert len(linked) == 1 and linked[0]["reason"] == "embedding", linked
    assert detect(far, embed_fn=fake_embed) == []

    # Over-merge guard: three genuinely different units stay three.
    spread = [
        {"kind": "experience", "id": "a", "text": "alpha", "header": "One"},
        {"kind": "experience", "id": "b", "text": "beta", "header": "Two"},
        {"kind": "experience", "id": "c", "text": "gamma", "header": "Three"},
    ]
    assert detect(spread, embed_fn=lambda ts: [[float(i), 1.0, 0.0] for i in range(len(ts))]) == []


def test_detector_gray_zone_needs_judge_and_degraded_is_advisory():
    import math

    from data.conflicts_detect import detect

    # Two unit-ish vectors whose cosine lands inside the 0.72..0.95 gray
    # window: 40 degrees apart in the first two coordinates.
    theta = math.radians(40)
    v = [1.0, math.tan(theta), 0.0]
    w = [1.0, 0.0, 0.0]

    def mid_embed(texts):
        return [v if i == 0 else w for i in range(len(texts))]

    pair = [
        {"kind": "experience", "id": "g1", "parent_id": "job-1", "text": "alpha one", "header": "A"},
        {"kind": "experience", "id": "g2", "parent_id": "job-1", "text": "alpha two", "header": "B"},
    ]
    cos = sum(a * b for a, b in zip(v, w, strict=False)) / (math.sqrt(sum(x * x for x in v)) * math.sqrt(sum(x * x for x in w)))
    assert 0.72 <= cos < 0.95, cos
    assert detect(pair, embed_fn=mid_embed) == []
    assert len(detect(pair, embed_fn=mid_embed, judge=lambda a, b: True)) == 1
    assert detect(pair, embed_fn=mid_embed, judge=lambda a, b: False) == []

    # Degraded provider: even a strong cosine stays judge-only (advisory).
    strong = [
        {"kind": "experience", "id": "s1", "parent_id": "job-1", "text": "built feed fan-out", "header": "A"},
        {"kind": "experience", "id": "s2", "parent_id": "job-1", "text": "built feed fan-out too", "header": "B"},
    ]
    def same(ts):
        return [[1.0, 0.0, 0.0] for _ in ts]
    assert detect(strong, embed_fn=same, semantic_advisory=True) == []
    assert len(detect(strong, embed_fn=same, semantic_advisory=True, judge=lambda a, b: True)) == 1


def test_detector_bridges_clusters_via_union_find_and_never_crosses_kinds():
    from data.conflicts_detect import detect

    # a-b link via exact bullet key, b-c via embedding: union-find merges all 3.
    def bridge_embed(texts):
        out = []
        for t in texts:
            if t.startswith("shared") or t.startswith("middle") or t.endswith("same"):
                out.append([1.0, 0.0])
            else:
                out.append([0.0, 1.0])
        return out

    pts = [
        {"kind": "project", "id": "a", "parent_id": "project-1", "text": "shared prefix", "header": "Data Platform"},
        {"kind": "project", "id": "b", "parent_id": "project-1", "text": "shared prefix", "header": "Data Platform (2020)"},
        {"kind": "project", "id": "c", "parent_id": "project-1", "text": "tail same", "header": "Unrelated Title"},
    ]
    groups = detect(pts, embed_fn=bridge_embed)
    assert len(groups) == 1, groups
    assert {m["id"] for m in groups[0]["members"]} == {"a", "b", "c"}, groups

    # Cross-kind pairs are never candidates, whatever the geometry says.
    cross = [
        {"kind": "skill", "id": "s1", "text": "identical copy", "header": "Go"},
        {"kind": "project", "id": "q1", "text": "identical copy", "header": "Go"},
    ]
    assert detect(cross, embed_fn=lambda ts: [[1.0] for _ in ts]) == []


def test_flatten_profile_units_returns_bullet_children_only():
    from data.conflicts_detect import flatten_profile_units

    profile = {
        "exp": [{
            "id": "e1", "role": "Controls Engineer", "co": "RoboDyne Labs",
            "d": "blob",
            "points": [{"id": "c1", "text": "bullet one"}],
        }],
        "projects": [{
            "id": "p1", "title": "Feed System", "impact": "impact blob",
            "points": [{"id": "c2", "text": "bullet two"}],
        }],
    }
    units = flatten_profile_units(profile)
    by_id = {(u["kind"], u["id"]): u for u in units}
    assert ("experience", "e1") not in by_id and ("experience", "c1") in by_id
    assert ("project", "p1") not in by_id and ("project", "c2") in by_id
    assert by_id[("experience", "c1")]["parent_id"] == "e1"
    assert by_id[("experience", "c1")]["header"] == "Controls Engineer RoboDyne Labs"
    assert by_id[("project", "c2")]["parent_id"] == "p1"
    assert by_id[("project", "c2")]["header"] == "Feed System"


# ── generation enforcement ────────────────────────────────────────────────────

class _FakeConflicts:
    def __init__(self, groups):
        self._groups = groups
        self.listed = False

    def list_groups(self):
        self.listed = True
        return self._groups


class _FakeRepo:
    def __init__(self, groups):
        self.conflicts = _FakeConflicts(groups)


def _profile():
    return {
        "skills": [{"id": "sk1", "n": "Rust"}],
        "projects": [
            {"id": "pr1", "title": "First", "points": [{"id": "pp1", "text": "one"}, {"id": "pp2", "text": "two"}]},
            {"id": "pr2", "title": "Second"},
        ],
        "exp": [
            {"id": "ex2", "role": "Same Role", "co": "Co", "points": [{"id": "xp2", "text": "b2"}]},
            {"id": "ex1", "role": "Same Role", "co": "Co", "points": [{"id": "xp1", "text": "b1"}]},
        ],
    }


def test_enforcement_picked_member_wins_over_profile_order():
    from generation.generators.package import _drop_conflict_duplicates

    repo = _FakeRepo([{
        "id": "g1", "status": "picked", "picked_kind": "experience", "picked_id": "xp2",
        "members": [
            {"point_kind": "experience", "point_id": "xp1"},
            {"point_kind": "experience", "point_id": "xp2"},
        ],
    }])
    scoped = _drop_conflict_duplicates(_profile(), repo)
    assert [row["id"] for row in scoped["exp"]] == ["ex2", "ex1"]
    assert [p["id"] for p in scoped["exp"][0]["points"]] == ["xp2"]
    assert scoped["exp"][1]["points"] == []
    # master superset untouched
    assert [row["id"] for row in _profile()["exp"]] == ["ex2", "ex1"]
    assert repo.conflicts.listed


def test_enforcement_unpicked_keeps_earliest_wording():
    from generation.generators.package import _drop_conflict_duplicates

    repo = _FakeRepo([{
        "id": "g1", "status": "open", "picked_kind": None, "picked_id": None,
        "members": [
            {"point_kind": "experience", "point_id": "xp2"},
            {"point_kind": "experience", "point_id": "xp1"},  # listed later; xp2 is earliest
        ],
    }])
    scoped = _drop_conflict_duplicates(_profile(), repo)
    assert [row["id"] for row in scoped["exp"]] == ["ex2", "ex1"]
    assert [p["id"] for p in scoped["exp"][0]["points"]] == ["xp2"]
    assert scoped["exp"][1]["points"] == []


def test_enforcement_keep_both_and_dismissed_leave_everything_in():
    from generation.generators.package import _drop_conflict_duplicates

    members = [
        {"point_kind": "experience", "point_id": "xp1"},
        {"point_kind": "experience", "point_id": "xp2"},
    ]
    for status in ("keep_both", "dismissed"):
        repo = _FakeRepo([{"id": "g1", "status": status, "picked_kind": None, "picked_id": None, "members": members}])
        scoped = _drop_conflict_duplicates(_profile(), repo)
        assert sorted(row["id"] for row in scoped["exp"]) == ["ex1", "ex2"]
        assert {p["id"] for row in scoped["exp"] for p in row.get("points") or []} == {"xp1", "xp2"}


def test_enforcement_out_of_scope_group_is_untouched_and_points_are_filtered():
    from generation.generators.package import _drop_conflict_duplicates

    # Group where only ONE member survives scoping (ex1 absent): no constraint.
    repo = _FakeRepo([{
        "id": "g1", "status": "open", "picked_kind": None, "picked_id": None,
        "members": [
            {"point_kind": "experience", "point_id": "ex1"},
            {"point_kind": "experience", "point_id": "gone"},
        ],
    }])
    single = {"skills": [], "projects": [], "exp": [{"id": "ex2", "role": "Only", "co": ""}]}
    scoped = _drop_conflict_duplicates(single, repo)
    assert [row["id"] for row in scoped["exp"]] == ["ex2"]

    # Point-level group INSIDE one project row: row kept, losing bullet dropped.
    profile = _profile()
    repo = _FakeRepo([{
        "id": "g2", "status": "open", "picked_kind": None, "picked_id": None,
        "members": [
            {"point_kind": "project", "point_id": "pp1"},
            {"point_kind": "project", "point_id": "pp2"},
        ],
    }])
    scoped = _drop_conflict_duplicates(profile, repo)
    assert [row["id"] for row in scoped["projects"]] == ["pr1", "pr2"]
    assert [p["id"] for p in scoped["projects"][0]["points"]] == ["pp1"]
