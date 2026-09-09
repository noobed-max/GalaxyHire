# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vasudev Siddh and vasu-devs
"""Tests for the lazy description hydration store path (update_lead_description)."""

import subprocess
import sys
import uuid
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(__file__).resolve().parent / ".scratch-lead-desc"


def _run(body: str) -> None:
    SCRATCH.mkdir(exist_ok=True)
    db_path = str(SCRATCH / f"leads-{uuid.uuid4().hex}.db")
    script = (
        "import sys; sys.path.insert(0, '.');"
        "from data.sqlite import leads as ld;"
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


def test_update_lead_description_hydrates_and_marks_meta():
    _run(
        "ld.save_lead({'job_id': 'j1', 'title': 'DevOps Engineer', 'company': 'Collibra',"
        " 'platform': 'greenhouse', 'description': 'short',"
        " 'source_meta': {'corpus_job_id': 'abc123'}}, db_path=db)\n"
        "updated = ld.update_lead_description('j1', 'FULL JD TEXT HERE', db_path=db)\n"
        "assert updated['description'] == 'FULL JD TEXT HERE', updated\n"
        "assert updated['source_meta']['description_hydrated'] is True, updated['source_meta']\n"
        "# corpus_job_id preserved\n"
        "assert updated['source_meta']['corpus_job_id'] == 'abc123'\n"
        "# missing lead -> None\n"
        "assert ld.update_lead_description('nope', 'x', db_path=db) is None\n"
    )
