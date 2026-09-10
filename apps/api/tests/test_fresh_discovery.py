"""Contract tests for an explicit dashboard Find collection.

All scraper calls here are fakes.  The tests exercise the intent boundary and the local, recoverable
lead store without contacting a live job board or corpus service.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from api.routers.discovery import _scrape_for
from data.sqlite.connection import connect, init_sql
from data.sqlite.leads import (
    retire_stale_discovery_leads,
    save_discovery_leads,
    save_lead,
)


class _FreshRunCorpus:
    def __init__(self) -> None:
        self.scrape_start_calls: list[dict] = []
        self.fresh_calls = 0
        self.active = False
        self.finished = asyncio.Event()
        self.hold = False

    async def scrape_fresh(self, *_args):
        self.fresh_calls += 1
        return True

    async def scrape_start(self, phrase, *, location="", portals=None):
        if self.active:
            return {"running": True, "conflict": True}
        self.active = True
        self.scrape_start_calls.append({"phrase": phrase, "location": location, "portals": portals})
        return {"running": True}

    async def scrape_status(self):
        if self.hold and self.active and not self.finished.is_set():
            return {"running": True, "status": "running"}
        self.active = False
        return {"running": False, "status": "done", "collected": 2}

    async def scrape_stop(self):
        self.active = False
        self.finished.set()


class _FailedRunCorpus:
    async def scrape_fresh(self, *_args):
        return False

    async def scrape_start(self, *args, **kwargs):
        return {
            "available": False,
            "error": "cannot start the scraper: no npm on the corpus service PATH",
        }


async def _broadcast(*_args):
    return None


def test_two_repeated_dashboard_find_clicks_start_two_fresh_scrapes(monkeypatch):
    corpus = _FreshRunCorpus()
    corpus.hold = False
    monkeypatch.setattr("api.routers.discovery.SCRAPE_POLL_S", 0)

    async def run():
        first = await _scrape_for(
            corpus, "software engineer", None, _broadcast,
            location="Berlin", portals=["greenhouse"], force_scrape=True,
        )
        corpus.finished.set()
        second = await _scrape_for(
            corpus, "software engineer", None, _broadcast,
            location="Berlin", portals=["greenhouse"], force_scrape=True,
        )
        return first, second

    first, second = asyncio.run(run())
    assert len(corpus.scrape_start_calls) == 2
    assert first["completed"] and second["completed"]
    assert corpus.fresh_calls == 0, "the explicit dashboard intent bypasses freshness"


def test_concurrent_find_does_not_start_a_second_scraper(monkeypatch):
    corpus = _FreshRunCorpus()
    corpus.hold = True
    monkeypatch.setattr("api.routers.discovery.SCRAPE_POLL_S", 0)

    async def run():
        first_task = asyncio.create_task(
            _scrape_for(corpus, "sre", None, _broadcast, force_scrape=True)
        )
        while not corpus.active:
            await asyncio.sleep(0)
        second = await _scrape_for(corpus, "sre", None, _broadcast, force_scrape=True)
        corpus.finished.set()
        first = await first_task
        return first, second

    first, second = asyncio.run(run())
    assert len(corpus.scrape_start_calls) == 1
    assert second["conflict"] is True
    assert first["completed"] is True


def test_non_dashboard_search_keeps_freshness_cache(monkeypatch):
    corpus = _FreshRunCorpus()
    monkeypatch.setattr("api.routers.discovery.SCRAPE_POLL_S", 0)
    outcome = asyncio.run(
        _scrape_for(corpus, "sre", None, _broadcast, force_scrape=False)
    )
    assert outcome["fresh_skipped"] is True
    assert not corpus.scrape_start_calls


def test_dashboard_find_surfaces_scraper_start_failure_without_corpus_fallback(monkeypatch):
    corpus = _FailedRunCorpus()
    monkeypatch.setattr("api.routers.discovery.SCRAPE_POLL_S", 0)
    outcome = asyncio.run(
        _scrape_for(corpus, "software engineer", None, _broadcast, force_scrape=True)
    )
    assert outcome["failed"] is True
    assert outcome["completed"] is False
    assert "no npm" in outcome["error"]


def _lead(job_id: str, url: str, *, status: str = "discovered") -> dict:
    return {
        "job_id": job_id,
        "title": "Software Engineer",
        "company": "Acme",
        "url": url,
        "platform": "greenhouse",
        "status": status,
    }


def test_duplicate_result_urls_save_one_and_preserve_applied_history(tmp_path: Path):
    db = str(tmp_path / "leads.db")
    init_sql(db)
    result = save_discovery_leads(
        [
            _lead("canonical-a", "https://jobs.acme.test/role?utm_source=one"),
            _lead("canonical-b", "https://JOBS.ACME.TEST/role/"),
        ],
        query="software engineer", db_path=db,
    )
    assert result["saved"] == 1 and result["deduplicated"] == 1

    save_lead(_lead("applied", "https://jobs.acme.test/applied"), db)
    conn = connect(db)
    try:
        conn.execute("UPDATE leads SET status='applied' WHERE job_id='applied'")
        conn.commit()
    finally:
        conn.close()
    duplicate = save_discovery_leads(
        [_lead("new-id", "https://jobs.acme.test/applied?utm_campaign=refresh")],
        query="software engineer", db_path=db,
    )
    assert duplicate["saved"] == 0 and duplicate["deduplicated"] == 1
    conn = connect(db)
    try:
        assert conn.execute("SELECT status FROM leads WHERE job_id='applied'").fetchone()[0] == "applied"
        assert conn.execute("SELECT count(*) FROM leads").fetchone()[0] == 2
    finally:
        conn.close()


def test_retirement_is_bounded_and_never_touches_applied_rows(tmp_path: Path):
    db = str(tmp_path / "leads.db")
    init_sql(db)
    scope = json.dumps({
        "discovery_scope": {
            "query": "software engineer", "location": "berlin", "portals": ["greenhouse"],
        }
    })
    conn = connect(db)
    try:
        conn.execute(
            "INSERT INTO leads(job_id,title,company,url,platform,status,source_meta,created_at) "
            "VALUES(?,?,?,?,?,?,?,datetime('now','-45 days'))",
            ("stale", "Old Engineer", "Acme", "https://acme.test/old", "greenhouse", "discovered", scope),
        )
        conn.execute(
            "INSERT INTO leads(job_id,title,company,url,platform,status,source_meta,created_at) "
            "VALUES(?,?,?,?,?,?,?,datetime('now','-45 days'))",
            ("historical", "Applied Engineer", "Acme", "https://acme.test/applied", "greenhouse", "applied", scope),
        )
        conn.commit()
    finally:
        conn.close()

    summary = retire_stale_discovery_leads(
        query="Software Engineer", location="Berlin", portals=["greenhouse"],
        fresh_leads=[_lead("fresh", "https://acme.test/new")], db_path=db,
    )
    assert summary["retired"] == 1
    conn = connect(db)
    try:
        statuses = dict(conn.execute("SELECT job_id,status FROM leads").fetchall())
        assert statuses == {"stale": "discarded", "historical": "applied"}
        assert conn.execute("SELECT count(*) FROM events WHERE job_id='stale'").fetchone()[0] == 1
    finally:
        conn.close()


def _seed_stale_with_scope(db: str, job_id: str, scope_extra: dict) -> None:
    scope = json.dumps({
        "discovery_scope": {
            "query": "software engineer", "location": "berlin", **scope_extra,
        }
    })
    conn = connect(db)
    try:
        conn.execute(
            "INSERT INTO leads(job_id,title,company,url,platform,status,source_meta,created_at) "
            "VALUES(?,?,?,?,?,?,?,datetime('now','-45 days'))",
            (job_id, "Old Engineer", "Acme", f"https://acme.test/{job_id}",
             "greenhouse", "discovered", scope),
        )
        conn.commit()
    finally:
        conn.close()


def _status_of(db: str, job_id: str) -> str:
    conn = connect(db)
    try:
        return str(conn.execute("SELECT status FROM leads WHERE job_id=?", (job_id,)).fetchone()[0])
    finally:
        conn.close()


def test_retirement_fails_closed_when_run_coverage_is_unknown(tmp_path: Path):
    # `portals=None` means the catalog was unavailable, so the run covered an unknown set of
    # boards. Unknown is not unlimited: nothing may be archived on that basis.
    db = str(tmp_path / "leads.db")
    init_sql(db)
    _seed_stale_with_scope(db, "stale", {"portals": ["greenhouse"]})

    summary = retire_stale_discovery_leads(
        query="software engineer", location="Berlin", portals=None,
        fresh_leads=[_lead("fresh", "https://acme.test/new")], db_path=db,
    )
    assert summary["retired"] == 0
    assert _status_of(db, "stale") == "discovered"


def test_retirement_keeps_rows_without_a_recorded_portal_scope(tmp_path: Path):
    # A row whose scope records no portals cannot prove it was superseded by this run.
    db = str(tmp_path / "leads.db")
    init_sql(db)
    _seed_stale_with_scope(db, "stale", {"portals": []})

    summary = retire_stale_discovery_leads(
        query="software engineer", location="Berlin", portals=["greenhouse"],
        fresh_leads=[_lead("fresh", "https://acme.test/new")], db_path=db,
    )
    assert summary["retired"] == 0
    assert _status_of(db, "stale") == "discovered"


def test_retirement_keeps_rows_from_portals_the_run_did_not_cover(tmp_path: Path):
    db = str(tmp_path / "leads.db")
    init_sql(db)
    _seed_stale_with_scope(db, "stale", {"portals": ["jobicy", "greenhouse"]})

    summary = retire_stale_discovery_leads(
        query="software engineer", location="Berlin", portals=["greenhouse"],
        fresh_leads=[_lead("fresh", "https://acme.test/new")], db_path=db,
    )
    assert summary["retired"] == 0
    assert _status_of(db, "stale") == "discovered"


def test_retirement_accepts_a_superset_run(tmp_path: Path):
    # The positive case: a run whose portal set covers every board the old row came from may
    # retire it. Over-tightening this would leak stale rows forever.
    db = str(tmp_path / "leads.db")
    init_sql(db)
    _seed_stale_with_scope(db, "stale", {"portals": ["greenhouse"]})

    summary = retire_stale_discovery_leads(
        query="software engineer", location="Berlin", portals=["arbeitnow", "greenhouse"],
        fresh_leads=[_lead("fresh", "https://acme.test/new")], db_path=db,
    )
    assert summary["retired"] == 1
    assert _status_of(db, "stale") == "discarded"
