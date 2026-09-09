"""DB-backed search integration: seed jobs, embed, search, golden-set nDCG, profile + API.

Skipped unless GALAXY_TEST_DB=1 with a live Postgres.
Run: make db-up && GALAXY_TEST_DB=1 uv run pytest tests/test_search_db.py
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker, migrate
from galaxy.dedup.engine import DedupEngine
from galaxy.ingestion.normalize import apply_derived_fields
from galaxy.ingestion.store import PostgresJobStore
from galaxy.models.enums import Site
from galaxy.models.job import Location, RawJobFields, SourceObservation
from galaxy.models.profile import Profile, Project, Skill
from galaxy.search import service
from galaxy.search.eval import GoldenQuery, evaluate
from galaxy.search.index import embed_pending
from galaxy.search.profiles import ProfileStore

pytestmark = pytest.mark.skipif(
    os.getenv("GALAXY_TEST_DB") != "1", reason="set GALAXY_TEST_DB=1 with a live Postgres"
)

NOW = datetime(2026, 7, 23, tzinfo=UTC)

# a tiny, controlled corpus with obvious relevance
CORPUS = [
    ("py1", "Senior Python Backend Engineer", "Build APIs with Python, Django, and Postgres. Remote.", True),
    ("py2", "Python Data Engineer", "Data pipelines in Python and Airflow. Remote friendly.", True),
    ("k8s", "Site Reliability Engineer", "Kubernetes, Go, Python for platform reliability. Remote.", True),
    ("fe1", "Frontend React Engineer", "Build UIs with React and TypeScript. Remote.", False),
    ("mk1", "Marketing Brand Manager", "Own brand strategy and campaigns. Onsite.", False),
]


async def _seed():
    await migrate()
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        await s.execute(text("DELETE FROM applications"))  # FK → canonical_jobs
        await s.execute(text("DELETE FROM source_observations"))
        await s.execute(text("DELETE FROM canonical_jobs"))
    obs = []
    for jid, title, desc, remote in CORPUS:
        f = RawJobFields(
            title=title, company="Acme", location=Location(remote=remote, country="USA"),
            description_md=desc,
        )
        apply_derived_fields(f)
        obs.append(SourceObservation(site=Site.GREENHOUSE, source_job_id=jid,
                                     url=f"https://x/{jid}", observed_at=NOW, raw_title=title, fields=f))
    jobs = DedupEngine().dedup(obs).jobs
    await PostgresJobStore().upsert_jobs(jobs)
    await embed_pending()
    # map external label -> canonical id via title
    by_title = {j.title: j.canonical_job_id for j in jobs}
    return by_title


@pytest.mark.asyncio
async def test_search_ranks_relevant_first_and_golden_ndcg():
    by_title = await _seed()
    py1 = by_title["Senior Python Backend Engineer"]
    py2 = by_title["Python Data Engineer"]
    k8s = by_title["Site Reliability Engineer"]
    mk1 = by_title["Marketing Brand Manager"]

    ranked = await service.search(search_term="python engineer", positive_skills=["python"], limit=10)
    ids = [j.canonical_job_id for j in ranked]
    assert py1 in ids and py2 in ids
    # marketing role must not outrank the python roles
    assert ids.index(py1) < ids.index(mk1) if mk1 in ids else True

    golden = [
        GoldenQuery(
            name="python",
            args={"search_term": "python engineer", "positive_skills": ["python"], "limit": 50},
            relevance={py1: 3, py2: 3, k8s: 1, mk1: 0},
        )
    ]
    res = await evaluate(golden)
    assert res.ndcg_at_10 >= 0.7  # relevant jobs ranked near the top
    assert res.recall_at_50 == 1.0  # all relevant retrieved


@pytest.mark.asyncio
async def test_negative_seniority_filter_excludes():
    await _seed()
    # exclude senior+ ; the "Senior Python Backend Engineer" must drop out
    ranked = await service.search(search_term="python engineer", max_seniority="mid", limit=50)
    assert all(j.seniority in (None, "intern", "junior", "mid") for j in ranked)


@pytest.mark.asyncio
async def test_profile_roundtrip_and_slice():
    uid = uuid4()
    profile = Profile(
        user_id=uid,
        roles=["swe"],
        skills=[Skill(name="Python", tags=["swe"])],
        projects=[Project(title="API", skills=["Python"], role_tags=["swe"])],
    )
    saved = await ProfileStore().upsert(profile)
    assert saved.profile_version == 1  # fresh insert
    resaved = await ProfileStore().upsert(saved)
    assert resaved.profile_version == 2  # conflict-update bumps the version (docs/05 §9)
    got = await ProfileStore().get(uid)
    assert got is not None
    assert got.skills[0].name == "Python"
    assert got.projects[0].id == profile.projects[0].id  # stable id preserved
