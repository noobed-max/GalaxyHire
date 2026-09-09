"""Portal catalog (`galaxy/scrape/catalog.py`) — the UI's source list, read from the scraper config.

Two contracts under test:

  1. `resolve_entry_id` must agree EXACTLY with the TS `resolveEntryId` in
     services/scraper-node/src/config.ts — the UI sends ids the Node worker resolves, and drift
     means "unknown portal id" 422s or silently-skipped boards. Both suites assert against the
     shared golden fixture `services/scraper-node/test/fixtures/entry-ids.json`.

  2. The catalog comes from the config file, never from what the corpus happens to hold —
     MAJOR-CHANGE/06 §2.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from galaxy.scrape.catalog import load_catalog, resolve_entry_id, slug_from_url

FIXTURE = Path(__file__).resolve().parents[2] / "scraper-node" / "test" / "fixtures" / "entry-ids.json"


def _cases():
    return json.loads(FIXTURE.read_text())["cases"]


class TestEntryIdAgreement:
    @pytest.mark.parametrize("case", _cases(), ids=lambda c: c["entry"].get("name", "?"))
    def test_matches_the_shared_golden_fixture(self, case):
        assert resolve_entry_id(case["entry"]) == case["id"]


class TestSlugRules:
    def test_generic_segments_are_not_slugs(self):
        assert slug_from_url("https://nvidia.wd5.myworkdayjobs.com/en-US/jobs") is None
        assert slug_from_url("https://whatever.com/") is None

    def test_workday_site_is_the_last_segment(self):
        assert slug_from_url("https://gis.wd1.myworkdayjobs.com/wday/cxs/gis/GIS/jobs") == "gis"


class TestCatalogFromConfig:
    def test_shipped_config_produces_a_catalog(self):
        sources = load_catalog()
        assert len(sources) > 60, "boards + curated tenants"
        ids = [s["id"] for s in sources]
        assert len(ids) == len(set(ids)), "two portals must never share one toggle id"
        assert "arbeitnow" in ids
        assert "greenhouse:anthropic" in ids  # tenants key as provider:slug

    def test_every_source_declares_a_policy_and_default(self):
        for s in load_catalog():
            assert s["recency_policy"] in ("strict", "first_seen", "off")
            assert isinstance(s["default_enabled"], bool)
            assert s["kind"] in ("board", "tenant")

    def test_a_custom_path_can_be_loaded(self, tmp_path):
        cfg = tmp_path / "portals.yml"
        cfg.write_text(
            "job_boards:\n  - name: X\n    provider: arbeitnow\n"
            "    gh: {recency: strict, toggle: true, region: eu}\n"
        )
        out = load_catalog(cfg)
        assert out == [
            {"id": "arbeitnow", "label": "X", "kind": "board", "provider": "arbeitnow",
             "region": "eu", "recency_policy": "strict", "default_enabled": True, "note": ""}
        ]

    def test_unquoted_off_is_not_read_as_strict(self, tmp_path):
        # YAML 1.1 booleans: a hand-edited `recency: off` without quotes loads as False.
        # Falling back to "strict" there would put a retired feed on the default scrape
        # with an enabled toggle — the worst possible default for a dead portal.
        cfg = tmp_path / "portals.yml"
        cfg.write_text(
            "job_boards:\n  - name: Dead\n    provider: echojobs\n"
            "    gh: {recency: off, toggle: false, note: retired}\n"
        )
        (out,) = load_catalog(cfg)
        assert out["recency_policy"] == "off"
        assert out["default_enabled"] is False
