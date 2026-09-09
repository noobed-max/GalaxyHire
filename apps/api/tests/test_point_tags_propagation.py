# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vasudev Siddh and auto-devs
"""Tests for point_tags batch propagation and multi-track accumulation.

Uses isolated subprocess execution to prevent sys.modules['sqlite3']
pollution from earlier mock test suites.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(__file__).resolve().parent / ".scratch-point-tags"


def _run_script(body: str, timeout: int = 40) -> str:
    SCRATCH.mkdir(exist_ok=True)
    run_dir = SCRATCH / f"run-{uuid.uuid4().hex}"
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(run_dir / "crm.db")
    script = (
        "import sys, os; sys.path.insert(0, '.');\n"
        f"os.environ['JHM_APP_DATA_DIR'] = {str(run_dir)!r};\n"
        "from data.sqlite.connection import init_sql, DEFAULT_DB_PATH;\n"
        f"db = {db_path!r};\n"
        "init_sql(db);\n"
        "init_sql();\n"
        + body
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        assert result.returncode == 0, f"Subprocess failed:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}"
        return result.stdout.strip()
    finally:
        for suffix in ("", "-wal", "-shm"):
            cand = Path(db_path + suffix)
            if cand.exists():
                try:
                    cand.unlink()
                except OSError:
                    pass


def test_add_point_tags_batch_multi_track_accumulation():
    """Test that adding new tags preserves previously added tags on the same points."""
    body = """
from data.sqlite import tags as tg
from data.sqlite import point_tags as pt

t_sde = tg.create_tag("SDE", db_path=db)["id"]
t_arch = tg.create_tag("ARCH", db_path=db)["id"]

# 1. First batch: tag as SDE
items_sde = [
    ("experience", "bullet_1", t_sde),
    ("skill", "skill_python", t_sde),
]
inserted = pt.add_point_tags_batch(items_sde, db_path=db)
assert inserted == 2

# Verify SDE is tagged
rows = pt.list_point_tags(db_path=db)
assert len(rows) == 2

# 2. Second batch: tag bullet_1 as ARCH as well, plus new bullet_2
items_arch = [
    ("experience", "bullet_1", t_arch),
    ("experience", "bullet_2", t_arch),
]
inserted_arch = pt.add_point_tags_batch(items_arch, db_path=db)
assert inserted_arch == 2

# bullet_1 must now have BOTH SDE and ARCH
all_rows = pt.list_point_tags(db_path=db)
bullet_1_tags = {r["tag_id"] for r in all_rows if r["point_id"] == "bullet_1"}
assert bullet_1_tags == {t_sde, t_arch}

# Scoping check: bullet_1 is included under both SDE and ARCH
ex_sde = pt.scoped_point_ids(t_sde, db_path=db)
assert "bullet_1" not in ex_sde.get("experience", set())

ex_arch = pt.scoped_point_ids(t_arch, db_path=db)
assert "bullet_1" not in ex_arch.get("experience", set())
# bullet_2 is excluded under SDE (since it only has ARCH)
assert "bullet_2" in ex_sde.get("experience", set())
print("OK")
"""
    assert _run_script(body) == "OK"


def test_add_point_tags_batch_idempotent_no_duplicates():
    """Test that re-inserting identical batches does not raise errors or duplicate rows."""
    body = """
from data.sqlite import tags as tg
from data.sqlite import point_tags as pt

t_sde = tg.create_tag("SDE", db_path=db)["id"]
items = [
    ("experience", "p1", t_sde),
    ("skill", "s1", t_sde),
]
assert pt.add_point_tags_batch(items, db_path=db) == 2
assert pt.add_point_tags_batch(items, db_path=db) in (0, 2)

rows = pt.list_point_tags(db_path=db)
assert len(rows) == 2
print("OK")
"""
    assert _run_script(body) == "OK"


def test_add_point_tags_batch_drops_invalid_and_unknown_tags():
    """Test that invalid kinds and non-existent tag IDs are rejected."""
    body = """
from data.sqlite import tags as tg
from data.sqlite import point_tags as pt

t_valid = tg.create_tag("ML", db_path=db)["id"]
items = [
    ("experience", "p1", t_valid),
    ("invalid_kind", "p2", t_valid),
    ("experience", "p3", "nonexistent-uuid"),
    ("skill", "", t_valid),
]
inserted = pt.add_point_tags_batch(items, db_path=db)
assert inserted == 1

rows = pt.list_point_tags(db_path=db)
assert len(rows) == 1
assert rows[0]["point_id"] == "p1"
print("OK")
"""
    assert _run_script(body) == "OK"


def test_profile_service_propagate_resume_point_tags():
    """Test that ProfileService correctly associates all extracted points with tag_id."""
    body = """
from data.sqlite import tags as tg
from data.sqlite import point_tags as pt
from profile.service import ProfileService
from models.schema import CandidateProfile, SkillEntry, ExperienceEntry, ProjectEntry

t_sde = tg.create_tag("SDE", db_path=db)["id"]

service = ProfileService()
parsed = CandidateProfile(
    n="Jane Doe",
    s="Applied AI engineer",
    skills=[SkillEntry(n="Python", cat="technical"), SkillEntry(n="FastAPI", cat="backend")],
    exp=[
        ExperienceEntry(
            role="Backend Intern",
            co="SuprMentr",
            period="2026",
            d="- Built distributed backend on GCP k3s.\\n- Configured Longhorn block storage."
        )
    ],
    projects=[
        ProjectEntry(
            title="Garage S3",
            stack=["Rust", "S3"],
            repo="https://github.com/test/garage",
            impact="- Self-hosted object storage serving media streams."
        )
    ],
)

count = service.propagate_resume_point_tags(parsed, t_sde, db_path=db)
# 2 skills + 1 exp entry + 2 exp points + 1 proj entry + 1 proj point = 7
assert count == 7

rows = pt.list_point_tags(db_path=db)
assert len(rows) == 7
assert all(r["tag_id"] == t_sde for r in rows)
print("OK")
"""
    assert _run_script(body) == "OK"


def test_profile_service_propagate_exact_matches_triples():
    """Test that exact matches with pre-existing point IDs are tagged with the new tag_id."""
    body = """
from data.sqlite import tags as tg
from data.sqlite import point_tags as pt
from profile.service import ProfileService
from models.schema import CandidateProfile, ExperienceEntry, ExactMatchBullet

t_arch = tg.create_tag("ARCH", db_path=db)["id"]

service = ProfileService()
parsed = CandidateProfile(
    n="Jane Doe",
    skills=[],
    exp=[
        ExperienceEntry(
            role="Staff Engineer",
            co="Acme Corp",
            period="2024 - 2026",
            d="Newly authored accomplishment point.",
            exact_matches=[
                ExactMatchBullet(existing_point_id="p_canon_123", text="Architected event bus.")
            ]
        )
    ],
    projects=[]
)

count = service.propagate_resume_point_tags(parsed, t_arch, db_path=db)
# 1 exp entry + 1 exact match canonical point + 1 newly authored point = 3
assert count == 3

rows = pt.list_point_tags(db_path=db)
tagged_ids = {r["point_id"] for r in rows}
assert "p_canon_123" in tagged_ids
print("OK")
"""
    assert _run_script(body) == "OK"


def test_end_to_end_resume_ingest_tag_propagation():
    """Test full async ingest_resume flow propagating tag_id to SQLite point_tags."""
    body = """
import asyncio
from data.sqlite import tags as tg
from data.sqlite import point_tags as pt
from profile.service import ProfileService
from models.schema import CandidateProfile, SkillEntry, ExperienceEntry
import profile.ingestor
import data.graph.profile

t_cloud = tg.create_tag("Cloud", db_path=db)["id"]

deterministic_profile = CandidateProfile(
    n="Cloud Architect",
    skills=[SkillEntry(n="Terraform", cat="devops")],
    exp=[
        ExperienceEntry(
            role="Cloud Architect",
            co="SkyNet",
            period="2025",
            d="Managed multi-region VPC peering."
        )
    ],
    projects=[]
)

profile.ingestor.ingest = lambda raw="", pdf_path=None, existing_profile=None: deterministic_profile

service = ProfileService()
data.graph.profile.save_profile_snapshot = lambda snapshot: None
data.graph.profile.forget_profile_deletions_for_profile = lambda snapshot: None

service.get_profile = lambda: {}
service.refresh_profile_snapshot = lambda: {}
service._sync_conflict_groups = lambda: None
async def _dummy_sync():
    return None
service._run_post_ingest_sync = _dummy_sync

async def main():
    result = await service.ingest_resume(raw="dummy resume text", tag_id=t_cloud)
    assert result.name == "Cloud Architect"

    rows = pt.list_point_tags(db_path=db)
    assert len(rows) >= 3  # 1 skill + 1 exp + 1 point
    assert all(r["tag_id"] == t_cloud for r in rows)
    print("OK")

asyncio.run(main())
"""
    assert _run_script(body) == "OK"
