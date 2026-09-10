"""Scrape run state (`galaxy/scrape/runner.py`).

The requirement was "refreshing the page should be able to dynamically detect the state of scraper
… like save states". React state dies with the tab and the in-memory TaskRegistry dies with the
process, so the state lives in Postgres and progress is *derived* rather than reported.

These tests cover the pieces that are pure logic. The database paths are exercised against the live
corpus in the integration run, since faking a session here would test the fake.

(The classes that guarded the app-reachability of the six Python anti-detect boards were deleted
with those boards: the connector set is career-ops' providers, and this file now guards the
portal-selection freshness rule instead.)
"""

from __future__ import annotations

import asyncio
import os

import pytest

from galaxy.scrape import runner
from galaxy.scrape.runner import (
    FRESH_FOR_HOURS,
    STALE_AFTER_SECONDS,
    ScrapeState,
    _alive,
    _portals_cover,
)


class TestLivenessCheck:
    def test_our_own_group_is_alive(self):
        # Checked as a group, not a single pid. `npm start` wraps `tsx`, so the recorded pid is
        # the wrapper's; if it ever exits while its children keep collecting, a single-pid check
        # would finalise a live run early. Defensive — no such failure has actually been observed.
        assert _alive(os.getpgid(0)) is True

    def test_no_pid_is_not_alive(self):
        # A run recorded before the pid was written back must not be treated as running forever.
        assert _alive(None) is False
        assert _alive(0) is False

    def test_an_absent_group_is_not_alive(self):
        # A group id that cannot exist must read as finished, so reconciliation finalises the row
        # rather than leaving it "running" until the stale sweep.
        assert _alive(2**22) is False


class TestStateReporting:
    def test_an_idle_state_says_nothing_is_running(self):
        state = ScrapeState()
        assert state.as_dict()["running"] is False
        assert state.as_dict()["status"] == "idle"

    def test_progress_and_phrase_are_reported_for_the_ui(self):
        body = ScrapeState(
            running=True, phrase="golang engineer", status="running",
            collected=74, elapsed_seconds=658.04, run_id=1,
        ).as_dict()
        assert body["phrase"] == "golang engineer"
        assert body["collected"] == 74
        # Rounded so the UI isn't re-rendering a number with six decimal places every poll.
        assert body["elapsed_seconds"] == 658.0

    def test_the_portal_set_is_reported(self):
        body = ScrapeState(portals=("arbeitnow", "jobicy")).as_dict()
        assert body["portals"] == ["arbeitnow", "jobicy"]

    def test_an_error_is_carried_not_swallowed(self):
        body = ScrapeState(status="failed", error="scraper exited 1").as_dict()
        assert body["status"] == "failed"
        assert "exited 1" in body["error"]


class TestStaleWindow:
    def test_the_stale_window_is_longer_than_a_real_scrape(self):
        # A full run takes minutes, not seconds. Reaping too early kills healthy scrapes and looks
        # exactly like the scraper crashing.
        assert STALE_AFTER_SECONDS > 30 * 60


class TestFreshnessKeepsSearchUsable:
    """Searching is what triggers collection now, so freshness is what stops it being unusable.

    Without it, pressing enter twice on the same phrase — or nudging a filter — would each cost a
    full scrape run. The window has to match the product's recency rule exactly: a run from 30
    hours ago cannot answer a 24-hour question.
    """

    def test_the_window_matches_the_product_window(self):
        assert FRESH_FOR_HOURS == 24, (
            "over 24h and a stale verdict serves jobs outside the product window; under it and "
            "filter iteration re-scrapes for nothing"
        )


class TestPortalSetFreshness:
    """A previous run is only 'fresh' for a portal set it actually covered (MAJOR-CHANGE/06 §5).

    The trap this prevents: search "software engineer" with 3 portals on, then with 40 — the 3-run
    would short-circuit the 40-search and the user silently gets 37 portals' worth of nothing.
    """

    def test_superset_recorded_run_answers_a_narrower_request(self):
        recorded = ("arbeitnow", "jobicy", "hackernews", "wttj")
        assert _portals_cover(("arbeitnow", "wttj"), recorded) is True

    def test_equal_sets_match(self):
        assert _portals_cover(("a", "b"), ("b", "a")) is True  # set semantics, not order

    def test_subset_recorded_run_never_answers_a_wider_request(self):
        # This is the bug the superset rule exists for.
        assert _portals_cover(("a", "b", "c"), ("a", "b")) is False

    def test_an_unrecorded_set_is_read_conservatively(self):
        # NULL portals = no set was recorded (pre-feature or hand-seeded row); it cannot vouch for
        # any selection, and after the pre-purge there are no such rows to be generous about.
        assert _portals_cover(("a",), None) is False
        assert _portals_cover(("a",), ()) is False

    def test_defaults_with_no_recorded_set_are_not_fresh(self):
        assert _portals_cover(None, None) is False


class TestFreshnessRequiresCompletion:
    """A stopped run must never satisfy freshness (fail-closed rule for partial coverage).

    Freshness means "this exact question was answered in full recently". A run the user stopped
    covered an unknown subset of the portals it recorded, so counting it as fresh would suppress
    the collection that completes the job — for the next 24 hours, with the UI reporting the
    question as already answered.
    """

    def _capture_query(self, monkeypatch) -> list[str]:
        captured: list[str] = []

        class Mappings:
            def all(self):
                return []

        class Result:
            def mappings(self):
                return Mappings()

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def execute(self, statement, params=None):
                captured.append(str(statement))
                return Result()

        monkeypatch.setattr(runner, "get_sessionmaker", lambda: Session)
        return captured

    def test_freshness_query_only_accepts_completed_runs(self, monkeypatch):
        captured = self._capture_query(monkeypatch)
        fresh = asyncio.run(runner.freshly_scraped("software engineer", portals=("arbeitnow",)))
        assert fresh is False
        assert captured, "freshness must consult scrape_runs"
        assert "status = 'done'" in captured[0]
        assert "stopped" not in captured[0]


@pytest.mark.asyncio
async def test_clear_history_stops_collection_and_deletes_only_run_metadata(monkeypatch):
    calls: list[str] = []

    class Result:
        rowcount = 4

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, statement):
            calls.append(str(statement))
            return Result()

        async def commit(self):
            calls.append("commit")

    async def fake_stop():
        calls.append("stop")
        return True

    monkeypatch.setattr(runner, "stop", fake_stop)
    monkeypatch.setattr(runner, "get_sessionmaker", lambda: Session)

    result = await runner.clear_history()

    assert result == {"cleared": 4, "stopped": True}
    assert calls[0] == "stop"
    assert any("DELETE FROM scrape_runs" in call for call in calls)
    assert not any("canonical_jobs" in call for call in calls)


class TestScrapeParameters:
    """The scrape takes phrase, hours, location, and the resolved portal set — nothing else.

    Location is handed to the boards (they support it and answer better for it); seniority,
    negatives and years stay post-hoc because boards apply them inconsistently. Portals decide
    WHICH boards get asked at all — see the UI selection contract in MAJOR-CHANGE/06 §4.
    """

    def test_start_accepts_location_and_portals(self):
        import inspect

        from galaxy.scrape.runner import start

        params = inspect.signature(start).parameters
        assert "location" in params
        assert "portals" in params
        # Hours default must equal the product window, not the old 48.
        assert params["hours"].default == FRESH_FOR_HOURS

    def test_freshness_is_keyed_on_location_and_portals(self):
        import inspect

        from galaxy.scrape.runner import freshly_scraped

        params = inspect.signature(freshly_scraped).parameters
        assert "location" in params
        assert "portals" in params


class TestSpawnContract:
    """The recorded truth and the spawned command must speak the same portal list.

    A `--portals` the runner never passes, or a set it records without passing, splits the
    product in two: the UI believes the selection was honored, the scraper ran the config
    defaults. This test reads the runner's own source so the two cannot drift silently — the
    exact failure pattern the deleted `_run_python_boards` import bug taught this repo about.
    """

    def test_start_builds_the_portals_argument(self):
        import inspect

        from galaxy.scrape import runner as mod

        src = inspect.getsource(mod.start)
        assert '"--portals"' in src
        assert '",".join(portals)' in src

    def test_spawn_failure_is_recorded_not_reconciled_as_done(self):
        # A missing npm (systemd units do not see nvm shims) must fail the row with the
        # reason on it: a pidless 'running' row would otherwise be reconciled to a
        # phantom 'done' that then satisfies the freshness key for 24h.
        import inspect

        from galaxy.scrape import runner as mod

        start_src = inspect.getsource(mod.start)
        resolver_src = inspect.getsource(mod._resolve_npm)
        assert "_resolve_npm()" in start_src
        assert 'shutil.which("npm")' in resolver_src
        assert "SCRAPER_NPM" in resolver_src
        assert ".nvm/versions/node" in resolver_src
        assert "status = 'failed'" in start_src
        reconcile_src = inspect.getsource(mod._reconcile)
        assert "spawn never recorded a pid" in reconcile_src

    def test_the_scrape_state_carries_portals_out_of_the_db(self):
        import inspect

        from galaxy.scrape import runner as mod

        assert "portals" in inspect.getsource(mod.current)
