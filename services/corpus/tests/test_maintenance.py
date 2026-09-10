"""Factory-reset purge of the stored corpus (galaxy/maintenance.py).

The Danger-zone full reset must delete the scraped jobs themselves. These tests pin the two
properties that matter: a live scrape is stopped first, and tables are deleted children-before-
parents so foreign keys never abort the wipe halfway.
"""

from __future__ import annotations

import asyncio

from galaxy import maintenance


class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _Session:
    created: list[_Session] = []

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.committed = False
        _Session.created.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, statement):
        self.statements.append(str(statement))
        return _Result(3)

    async def commit(self):
        self.committed = True


def test_purge_corpus_stops_scrapes_and_deletes_children_before_parents(monkeypatch):
    _Session.created = []

    stopped: list[bool] = []

    async def fake_stop() -> bool:
        stopped.append(True)
        return True

    # get_sessionmaker() returns the class; the `async with sm()` in purge_corpus instantiates it.
    monkeypatch.setattr(maintenance, "get_sessionmaker", lambda: _Session)
    monkeypatch.setattr(maintenance.runner, "stop", fake_stop)

    result = asyncio.run(maintenance.purge_corpus())

    assert stopped == [True], "a running scrape must be stopped before its rows are deleted"
    assert len(_Session.created) == 1
    session = _Session.created[0]
    assert session.committed is True
    tables = [str(statement).split("DELETE FROM ", 1)[1] for statement in session.statements]
    assert tables == list(maintenance._TABLES_CHILD_FIRST), "FK children must be deleted first"
    assert tables.index("applications") < tables.index("canonical_jobs")
    assert tables.index("source_observations") < tables.index("canonical_jobs")
    assert tables.index("applications") < tables.index("profiles")
    assert result["stopped"] is True
    assert result["purged"]["canonical_jobs"] == 3
    assert set(result["purged"]) == set(maintenance._TABLES_CHILD_FIRST)
