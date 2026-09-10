"""Tests for the danger-zone data reset (data/maintenance.py + the endpoint body).

The SQLite clear is verified against an injected fake connection so the test is
fast, deterministic, never touches a real database, and sidesteps the harness's
global sqlite3 fake. Graph/vector/asset clearing is stubbed per-test so the
orchestration + summary + settings-preservation logic is asserted in isolation.
"""

from __future__ import annotations

import pytest

import data.maintenance as maintenance


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Minimal sqlite connection stand-in: lists tables and records DELETEs."""

    def __init__(self, tables):
        self._tables = tables
        self.deleted: list[str] = []          # whole-table DELETE FROM <name>
        self.deleted_keys: list[str] = []     # keys hit by DELETE FROM settings WHERE key IN (...)
        self.committed = False

    def execute(self, sql, params=None):
        text = sql.strip()
        if text.upper().startswith("SELECT NAME FROM SQLITE_MASTER"):
            return _FakeCursor([{"name": t} for t in self._tables])
        if text.upper().startswith("DELETE FROM "):
            target = text[len("DELETE FROM "):].strip()
            if " WHERE " in target.upper():
                # Targeted profile-key delete (DELETE FROM settings WHERE key IN ...).
                self.deleted_keys.extend(params or [])
            else:
                self.deleted.append(target)
            return _FakeCursor([])
        return _FakeCursor([])

    def commit(self):
        self.committed = True


# A representative crm.db table set: user data + config/schema tables.
_TABLES = ["leads", "events", "error_log", "gateway_jobs", "settings", "resume_templates", "schema_migrations"]


def _inject_conn(monkeypatch, conn):
    import data.sqlite.connection as sc

    monkeypatch.setattr(sc, "get_connection", lambda *a, **k: conn)


def test_reset_sqlite_data_only_preserves_settings_and_templates(monkeypatch):
    conn = _FakeConn(_TABLES)
    _inject_conn(monkeypatch, conn)

    summary = {"sqlite_cleared": [], "errors": []}
    maintenance._reset_sqlite(summary, clear_settings=False)

    deleted = set(conn.deleted)
    # User data is wiped...
    assert {"leads", "events", "error_log", "gateway_jobs"} <= deleted
    # ...but the settings, resume-templates and schema tables survive as tables.
    assert "settings" not in deleted
    assert "resume_templates" not in deleted
    assert "schema_migrations" not in deleted
    assert conn.committed is True
    assert "leads" in summary["sqlite_cleared"]

    # ...HOWEVER the profile snapshot + identity rows live INSIDE the settings table
    # and are profile DATA, so they must be cleared even when settings are preserved
    # (otherwise get_profile() returns the cached snapshot and the profile still shows).
    from data.graph.profile_base import IDENTITY_KEYS, PROFILE_SNAPSHOT_KEY

    assert PROFILE_SNAPSHOT_KEY in conn.deleted_keys
    assert set(IDENTITY_KEYS) <= set(conn.deleted_keys)
    assert "settings:profile" in summary["sqlite_cleared"]


def test_reset_sqlite_full_clears_settings_and_templates(monkeypatch):
    conn = _FakeConn(_TABLES)
    _inject_conn(monkeypatch, conn)

    summary = {"sqlite_cleared": [], "errors": []}
    maintenance._reset_sqlite(summary, clear_settings=True)

    deleted = set(conn.deleted)
    assert {"leads", "events", "settings", "resume_templates"} <= deleted
    # Schema migration history is never wiped (would break the DB).
    assert "schema_migrations" not in deleted


def test_reset_all_data_orchestrates_every_store(monkeypatch):
    conn = _FakeConn(_TABLES)
    _inject_conn(monkeypatch, conn)
    # Stub the non-sqlite stores so the test never touches real Kuzu/LanceDB/disk.
    monkeypatch.setattr(maintenance, "_reset_graph", lambda s: s["graph_cleared"].append("Candidate"))
    monkeypatch.setattr(maintenance, "_reset_vectors", lambda s: s["vectors_dropped"].append("skills"))
    monkeypatch.setattr(maintenance, "_reset_assets", lambda s: s.__setitem__("assets_removed", 3))

    summary = maintenance.reset_all_data(clear_settings=False)

    assert "leads" in summary["sqlite_cleared"]
    assert "settings" not in summary["sqlite_cleared"]  # preserved by default
    assert summary["graph_cleared"] == ["Candidate"]
    assert summary["vectors_dropped"] == ["skills"]
    assert summary["assets_removed"] == 3
    assert summary["settings_cleared"] is False
    assert summary["errors"] == []


def test_reset_vectors_drops_every_table(monkeypatch):
    """Regression: lancedb's list_tables() returns a ListTablesResponse (iterating
    it yields tuples, not names), so the reset must go through vec_table_names().
    Every profile vector table must be dropped or the profile rebuilds from vectors."""
    dropped: list[str] = []

    class _FakeVec:
        def drop_table(self, name):
            dropped.append(name)

    monkeypatch.setattr("data.graph.profile_vectors.vec_table_names", lambda: ["skills", "projects", "experiences"])
    monkeypatch.setattr("data.vector.connection.vec", _FakeVec())

    summary = {"vectors_dropped": [], "errors": []}
    maintenance._reset_vectors(summary)

    assert set(dropped) == {"skills", "projects", "experiences"}
    assert set(summary["vectors_dropped"]) == {"skills", "projects", "experiences"}
    assert summary["errors"] == []


def test_reset_data_body_requires_explicit_confirm():
    from pydantic import ValidationError

    from core.types import ResetDataBody

    # The happy path: explicit confirmation.
    body = ResetDataBody(confirm="DELETE")
    assert body.confirm == "DELETE"
    assert body.clear_settings is False

    # No body / wrong token / extra field must all be rejected — a reset can never
    # fire by accident.
    with pytest.raises(ValidationError):
        ResetDataBody()
    with pytest.raises(ValidationError):
        ResetDataBody(confirm="yes")
    with pytest.raises(ValidationError):
        ResetDataBody(confirm="DELETE", surprise=True)


def test_full_reset_purges_the_corpus_while_data_reset_only_clears_history(monkeypatch):
    """The Danger-zone checkbox must actually delete the stored scraped jobs.

    Data-only resets keep the reusable corpus by design; the full factory reset must call the
    corpus purge (canonical jobs included), or "Delete everything" leaves hundreds of jobs behind.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import api.routers.settings as settings_module

    calls: dict[str, int] = {}

    class FakeTasks:
        async def stop(self, _name):
            return False

        async def join(self, _name):
            return None

    class FakeCorpus:
        async def purge_corpus(self):
            calls["purge"] = calls.get("purge", 0) + 1
            return {"available": True, "purged": {"canonical_jobs": 759}, "stopped": False}

        async def scrape_clear_history(self):
            calls["history"] = calls.get("history", 0) + 1
            return {"available": True, "cleared": 1, "stopped": False}

    monkeypatch.setattr(settings_module, "search_tasks", FakeTasks())
    monkeypatch.setattr(settings_module, "get_corpus_discovery_service", lambda: FakeCorpus())
    monkeypatch.setattr(
        "data.maintenance.reset_all_data",
        lambda *, clear_settings=False: {
            "sqlite_cleared": [], "errors": [], "settings_cleared": clear_settings,
        },
    )
    monkeypatch.setattr("llm.client.reset_client_cache", lambda: None)

    app = FastAPI()
    app.include_router(settings_module.create_router(None, None))
    client = TestClient(app)

    data_only = client.post("/api/v1/data/reset", json={"confirm": "DELETE"})
    assert data_only.status_code == 200
    data_summary = data_only.json()["summary"]
    assert data_summary["scrape_history"] == {"available": True, "cleared": 1, "stopped": False}
    assert "corpus_purged" not in data_summary
    assert calls == {"history": 1}

    full = client.post("/api/v1/data/reset", json={"confirm": "DELETE", "clear_settings": True})
    assert full.status_code == 200
    full_summary = full.json()["summary"]
    assert full_summary["corpus_purged"]["purged"]["canonical_jobs"] == 759
    assert "scrape_history" not in full_summary
    assert calls == {"history": 1, "purge": 1}
