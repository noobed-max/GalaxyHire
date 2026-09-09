# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vasudev Siddh and vasu-devs
"""Tests for the doc_selections store behind the approval-doc-pane: the
(job, tag)-scoped upsert/get/delete roundtrip and the preset list."""

import subprocess
import sys
import uuid
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(__file__).resolve().parent / ".scratch-doc-selections"


def _run(body: str) -> None:
    SCRATCH.mkdir(exist_ok=True)
    db_path = str(SCRATCH / f"docsel-{uuid.uuid4().hex}.db")
    script = (
        "import sys; sys.path.insert(0, '.');"
        "from data.sqlite import doc_selections as ds;"
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


def test_doc_selections_roundtrip_and_scope_overwrite():
    _run(
        "sel = {'version': 1, 'skills_on': ['a'], 'points_on': ['p1']}\n"
        "r = ds.upsert('job1', 'tag1', sel, db_path=db)\n"
        "assert r['ok'] and r['updated_at'], r\n"
        "assert ds.get('job1', 'tag1', db_path=db) == sel\n"
        "# nothing at an untouched scope\n"
        "assert ds.get('job9', 'tag1', db_path=db) is None\n"
        "# re-saving the same scope overwrites (UNIQUE(job_id, tag_id))\n"
        "ds.upsert('job1', 'tag1', {'version': 2}, db_path=db)\n"
        "assert ds.get('job1', 'tag1', db_path=db) == {'version': 2}\n"
        "# delete removes exactly that scope\n"
        "assert ds.delete('job1', 'tag1', db_path=db)\n"
        "assert ds.get('job1', 'tag1', db_path=db) is None\n"
        "assert not ds.delete('job1', 'tag1', db_path=db)\n"
    )


def test_doc_presets_list_and_replace():
    _run(
        "sel = {'version': 1, 'projects_on': ['x']}\n"
        "a = ds.save_preset('Nurse ICU', '', sel, db_path=db)\n"
        "b = ds.save_preset('Welder', 'tagX', sel, db_path=db)\n"
        "presets = ds.list_presets(db_path=db)\n"
        "assert {p['name'] for p in presets} == {'Nurse ICU', 'Welder'}, presets\n"
        "by_name = {p['name']: p for p in presets}\n"
        "assert by_name['Nurse ICU']['selection'] == sel\n"
        "assert by_name['Welder']['tag_id'] == 'tagX'\n"
        "# same name -> same id -> replace\n"
        "ds.save_preset('Nurse ICU', '', {'version': 9}, db_path=db)\n"
        "presets = {p['name']: p for p in ds.list_presets(db_path=db)}\n"
        "assert presets['Nurse ICU']['selection'] == {'version': 9}\n"
        "# a new preset on an occupied tag scope replaces the occupant\n"
        "ds.save_preset('Second track', 'tagX', sel, db_path=db)\n"
        "names = {p['name'] for p in ds.list_presets(db_path=db)}\n"
        "assert names == {'Nurse ICU', 'Second track'}, names\n"
        "# deleting presets\n"
        "assert ds.delete_preset(a['id'], db_path=db)\n"
        "assert [p['name'] for p in ds.list_presets(db_path=db)] == ['Second track']\n"
        "assert not ds.delete_preset('missing-id', db_path=db)\n"
        "# job-scoped rows never leak into the preset list\n"
        "ds.upsert('some-job', 'tagX', sel, db_path=db)\n"
        "names = {p['name'] for p in ds.list_presets(db_path=db)}\n"
        "assert names == {'Second track'} and ds.get('some-job', 'tagX', db_path=db) == sel\n"
    )


def test_save_preset_requires_name():
    _run(
        "\n"
        "try:\n"
        "    ds.save_preset('', '', {}, db_path=db)\n"
        "    raise SystemExit('expected ValueError')\n"
        "except ValueError:\n"
        "    pass\n"
    )
