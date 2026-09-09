# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vasudev Siddh and vasu-devs
"""Tests for bullet-level profile-point tags: the sqlite store, the scoping
semantics (untagged points are universal, other-tag points excluded), and the
profile filter applied before drafting."""

import subprocess
import sys
import uuid
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(__file__).resolve().parent / ".scratch-point-tags"


def _run(body: str) -> None:
    SCRATCH.mkdir(exist_ok=True)
    db_path = str(SCRATCH / f"ptags-{uuid.uuid4().hex}.db")
    script = (
        "import sys; sys.path.insert(0, '.');"
        "from data.sqlite import point_tags as pt;"
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


def test_point_tags_store_roundtrip_and_scoping():
    _run(
        "from data.sqlite import tags as tg\n"
        "t1 = tg.create_tag('ML', db_path=db)['id']\n"
        "t2 = tg.create_tag('SWE', db_path=db)['id']\n"
        "# unknown tag id is dropped, known ones kept\n"
        "rows = pt.set_point_tags('project', 'p1', ['nope-should-be-ignored', t1, t2], db_path=db)\n"
        "assert {(r['point_kind'], r['point_id'], r['tag_id']) for r in rows} == {\n"
        "    ('project', 'p1', t1), ('project', 'p1', t2)}, rows\n"
        "# replace-set semantics\n"
        "rows = pt.set_point_tags('project', 'p1', [t2], db_path=db)\n"
        "assert [r['tag_id'] for r in rows] == [t2], rows\n"
        "# untagged point is universal -> never excluded\n"
        "pt.set_point_tags('project', 'p2', [], db_path=db)\n"
        "ex = pt.scoped_point_ids(t1, db_path=db)\n"
        "assert ex.get('project') == {'p1'}, ex\n"
        "# multi-tag membership keeps the point in\n"
        "t9 = tg.create_tag('Arch', db_path=db)['id']\n"
        "pt.set_point_tags('skill', 's1', [t1, t9], db_path=db)\n"
        "ex = pt.scoped_point_ids(t1, db_path=db)\n"
        "assert 's1' not in ex.get('skill', set()), ex\n"
        "# unknown kind rejected\n"
        "try:\n"
        "    pt.set_point_tags('achievement', 'a1', [], db_path=db)\n"
        "    raise SystemExit('expected ValueError')\n"
        "except ValueError:\n"
        "    pass\n"
        "# deleting a tag cleans up its point rows\n"
        "from data.sqlite import connection as cn\n"
        "tg.delete_tag(t2, db_path=db)\n"
        "conn = cn.connect(db)\n"
        "n = conn.execute('SELECT COUNT(*) FROM point_tags WHERE tag_id=?', (t2,)).fetchone()[0]\n"
        "conn.close()\n"
        "assert n == 0, n\n"
    )


def test_scope_profile_to_tag_filters_only_tagged_out_points():
    from generation.generators.package import _scope_profile_to_tag

    class _FakePointTags:
        @staticmethod
        def scoped_point_ids(tag_id):
            assert tag_id == "ml"
            return {"project": {"web1"}, "experience": {"exp1"}}

    class _FakeRepo:
        point_tags = _FakePointTags()

    profile = {
        "skills": [{"id": "sk1", "n": "Rust"}, {"id": "sk2", "n": "PyTorch"}],
        "projects": [{"id": "web1", "title": "L7LB"}, {"id": "ml1", "title": "Retrieval"}],
        "exp": [{"id": "exp1", "role": "A"}, {"id": "exp2", "role": "B"}],
    }
    # No tag selected -> master superset untouched.
    assert _scope_profile_to_tag(profile, "", _FakeRepo()) is profile
    scoped = _scope_profile_to_tag(profile, "ml", _FakeRepo())
    assert [p["id"] for p in scoped["projects"]] == ["ml1"]
    assert [e["id"] for e in scoped["exp"]] == ["exp2"]
    assert [s["id"] for s in scoped["skills"]] == ["sk1", "sk2"]
    # Original superset is not mutated.
    assert [p["id"] for p in profile["projects"]] == ["web1", "ml1"]
