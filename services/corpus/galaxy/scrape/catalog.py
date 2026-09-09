"""The portal catalog the UI toggle column renders — read from the scraper's config.

The catalog IS `services/scraper-node/config/portals.yml` (MAJOR-CHANGE/06 §2): the file that
decides what actually gets scraped, so the UI can never promise a portal the worker will not
visit. Never built from `SELECT DISTINCT site` — that shows only what worked last time and hides
what is broken, making failures undeclarable.

The id rule here (`resolve_entry_id`) MUST agree with
`services/scraper-node/src/config.ts::resolveEntryId` — the UI sends ids that the Node worker
resolves, and a mismatch means "unknown portal id" errors or silently-skipped boards. The
shared fixture `services/scraper-node/test/fixtures/entry-ids.json` pins both implementations
against the same expected outputs (`tests/test_portal_catalog.py` and the scraper's
`test/harness.test.ts` both assert against it).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

#: Where the scraper's config lives, relative to this service (same anchor as runner.SCRAPER_DIR).
CONFIG_DIR = Path(__file__).resolve().parents[3] / "scraper-node" / "config"

# Mirrors slugFromUrl in src/config.ts — patterns copied 1:1 from career-ops' own resolvers.
_SLUG_PATTERNS = [
    r"boards-api\.greenhouse\.io/v1/boards/([a-z0-9-]+)",
    r"job-boards(?:\.eu)?\.greenhouse\.io/([a-z0-9-]+)",
    r"boards\.greenhouse\.io/([a-z0-9-]+)",
    r"jobs\.ashbyhq\.com/([a-z0-9-]+)",
    r"jobs(?:\.eu)?\.lever\.co/([a-z0-9-]+)",
    r"([a-z0-9-]+)\.greenhouse\.job-boards\.com",
    r"apply\.workable\.com/([a-z0-9-]+)",
    r"jobs\.smartrecruiters\.com/api/v2/([a-z0-9-]+)",
    r"([a-z0-9-]+)\.teamtailor\.com",
    r"([a-z0-9-]+)\.recruitee\.com",
    r"([a-z0-9-]+)\.bamboohr\.com",
    r"([a-z0-9-]+)\.breezy\.hr",
    r"([a-z0-9-]+)\.jibeapply\.com",
    # cxs shape first: /wday/cxs/<tenant>/<site>/jobs → the SITE segment. Checked before the
    # generic fallback because the fallback would capture 'cxs' (a rejected segment) instead.
    r"myworkdayjobs\.com/(?:[^/]+/)*cxs/[^/]+/([a-z0-9-]+)",
    # plain hosted board: /<locale>/<site>/jobs → the first non-generic segment after the host
    r"myworkdayjobs\.com/[^/]+/([^/?#]+)",
    r"([a-z0-9-]+)\.myworkday\.com",
]
_SLUG_RES = [re.compile(p, re.IGNORECASE) for p in _SLUG_PATTERNS]
# Generic path segments that look like slugs but identify nothing.
_REJECT = re.compile(r"^(en|en-us|api|jobs|careers|wday|cxs)$", re.IGNORECASE)


def slug_from_url(url: str) -> str | None:
    for rx in _SLUG_RES:
        m = rx.search(url or "")
        if m:
            raw = m.group(m.lastindex or 1)
            if raw and len(raw) >= 2 and not _REJECT.match(raw):
                return re.sub(r"[^a-z0-9-]", "", raw.lower())
    return None


def resolve_entry_id(entry: dict) -> str:
    provider = str(entry.get("provider") or "").strip()
    url = str(entry.get("api") or entry.get("careers_url") or "")
    slug = slug_from_url(url)
    if not provider:
        return slug or str(entry.get("name") or "")
    return f"{provider}:{slug}" if slug else provider


def config_path() -> Path | None:
    """portals.yml, else the shipped example — the same order as src/config.ts::resolveConfigPath."""
    for candidate in (CONFIG_DIR / "portals.yml", CONFIG_DIR / "portals.example.yml"):
        if candidate.exists():
            return candidate
    return None


def is_fetchable(entry: dict) -> bool:
    """Mirrors src/config.ts::isDirectlyFetchable (websearch-only and parser entries can't run here)."""
    if entry.get("scan_method") == "websearch" and not entry.get("api"):
        return False
    if entry.get("parser"):
        return False
    return bool(entry.get("api") or entry.get("careers_url") or entry.get("provider"))


def load_catalog(path: Path | None = None) -> list[dict]:
    """Every scrapable portal, shaped for the UI: id, label, kind, policy, default state."""
    file = path or config_path()
    if file is None:
        return []
    doc = yaml.safe_load(file.read_text()) or {}
    out: list[dict] = []
    for kind, key in (("board", "job_boards"), ("tenant", "tracked_companies")):
        for entry in doc.get(key) or []:
            if not isinstance(entry, dict) or entry.get("enabled") is False or not is_fetchable(entry):
                continue
            gh = entry.get("gh") or {}
            # YAML 1.1 booleans strike here: an unquoted `off`/`on` loads as False/True, so a
            # hand-edited `recency: off` arrives as False and would default to "strict" — the
            # worst possible fallback for a retired feed. (The shipped config quotes "off";
            # this guard is for every future edit that forgets.)
            recency_raw = gh.get("recency")
            if recency_raw is False:
                recency_raw = "off"
            recency = recency_raw if recency_raw in ("strict", "first_seen", "off") else "strict"
            out.append({
                "id": resolve_entry_id(entry),
                "label": str(entry.get("name") or ""),
                "kind": kind,
                "provider": str(entry.get("provider") or ""),
                "region": gh.get("region") or "",
                "recency_policy": recency,
                "default_enabled": bool(gh.get("toggle", recency != "off")),
                "note": gh.get("note") or "",
            })
    return out
