"""Global cross-run near-duplicate dedup (docs/03 §2 — the 'fuzzy second pass').

`DedupEngine` only compares observations WITHIN one ingest run, so a job scraped Monday and its
near-twin scraped Wednesday — same role, but a drifted title/location gives it a different
canonical id — never get compared and both survive. This pass closes that gap: it scans the whole
DB, blocks by company, finds near-duplicate canonical jobs, and merges each cluster into one
survivor.

Merge criteria (deliberately conservative — over-merging is worse than a missed dup):
  same company_key  AND  (identical normalized title OR high title-token overlap)
  AND  compatible location (equal, or one side remote/unknown)
  AND  descriptions are SimHash-near (same threshold the in-batch guard uses) when both exist.

The merge is surgical: the survivor keeps its canonical_job_id (and pgvector row — nulled to force
a re-embed since the merged text may change); every observation and application is re-pointed to it;
the losers are deleted. No re-scrape — observations already carry their raw fields.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import structlog
from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker
from galaxy.dedup.engine import DedupEngine
from galaxy.dedup.simhash import SIMHASH_MERGE_THRESHOLD, hamming, simhash
from galaxy.ingestion.normalize import norm_location, norm_title
from galaxy.ingestion.store import PostgresJobStore
from galaxy.models.enums import Site
from galaxy.models.job import RawJobFields, SourceObservation

log = structlog.get_logger(__name__)

TITLE_JACCARD_MIN = 0.8


@dataclass
class GlobalDedupStats:
    scanned: int = 0
    clusters_merged: int = 0
    jobs_removed: int = 0


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _location_compatible(a: dict, b: dict) -> bool:
    """Same place, or one side is remote / has no location (unknown never blocks a merge)."""
    if a.get("remote") or b.get("remote"):
        return True
    na = norm_location(a.get("city"), a.get("country"), False)
    nb = norm_location(b.get("city"), b.get("country"), False)
    if not na or not nb:
        return True
    return na == nb


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def _near_dup(gi: dict, gj: dict, threshold: int) -> bool:
    same_title = gi["_toks"] == gj["_toks"] or _jaccard(gi["_toks"], gj["_toks"]) >= TITLE_JACCARD_MIN
    if not same_title:
        return False
    if not _location_compatible(gi["_loc"], gj["_loc"]):
        return False
    if gi["description_md"] and gj["description_md"]:
        return hamming(gi["_sim"], gj["_sim"]) <= threshold
    # no description on one side — require identical normalized titles (the strongest signal we have)
    return gi["_toks"] == gj["_toks"]


def _plan_company(group: list[dict], threshold: int) -> list[tuple[str, list[str]]]:
    """Return (survivor_id, loser_ids) merges for one company's jobs."""
    for g in group:
        g["_toks"] = set(norm_title(g["title"] or "").split())
        g["_sim"] = simhash(g["description_md"] or "")
        g["_loc"] = g["location"] if isinstance(g["location"], dict) else {}
    uf = _UnionFind(len(group))
    for i in range(len(group)):
        for j in range(i + 1, len(group)):
            if _near_dup(group[i], group[j], threshold):
                uf.union(i, j)
    clusters: dict[int, list[int]] = {}
    for idx in range(len(group)):
        clusters.setdefault(uf.find(idx), []).append(idx)

    plan: list[tuple[str, list[str]]] = []
    for members_idx in clusters.values():
        if len(members_idx) < 2:
            continue
        members = [group[i] for i in members_idx]
        # survivor = richest provenance (most observations), tie-break to the earliest-seen row
        survivor = max(members, key=lambda m: (m["obs_count"], -m["first_seen_at"].timestamp()))
        sid = survivor["canonical_job_id"]
        losers = [m["canonical_job_id"] for m in members if m["canonical_job_id"] != sid]
        plan.append((sid, losers))
    return plan


async def run_global_dedup(threshold: int = SIMHASH_MERGE_THRESHOLD) -> GlobalDedupStats:
    sm = get_sessionmaker()
    stats = GlobalDedupStats()

    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT c.canonical_job_id, c.company_key, c.title, c.location, c.description_md, "
                    "c.first_seen_at, "
                    "(SELECT count(*) FROM source_observations o "
                    " WHERE o.canonical_job_id = c.canonical_job_id) AS obs_count "
                    "FROM canonical_jobs c WHERE c.status = 'open'"
                )
            )
        ).mappings().all()

    stats.scanned = len(rows)
    by_company: dict[str, list[dict]] = {}
    for r in rows:
        by_company.setdefault(r["company_key"] or "", []).append(dict(r))

    plan: list[tuple[str, list[str]]] = []
    for company, group in by_company.items():
        if company and len(group) > 1:
            plan.extend(_plan_company(group, threshold))

    for survivor_id, losers in plan:
        await _merge_cluster(survivor_id, losers)
        stats.clusters_merged += 1
        stats.jobs_removed += len(losers)

    if stats.jobs_removed:
        log.info("global_dedup.done", **asdict(stats))
    return stats


async def _merge_cluster(survivor_id: str, losers: list[str]) -> None:
    sm = get_sessionmaker()
    all_ids = [survivor_id, *losers]

    # 1. reconstruct every observation in the cluster from its retained raw fields
    async with sm() as s:
        obs_rows = (
            await s.execute(
                text(
                    "SELECT site, source_job_id, url, raw_title, raw_fields, first_observed_at "
                    "FROM source_observations WHERE canonical_job_id = ANY(:ids)"
                ),
                {"ids": all_ids},
            )
        ).mappings().all()

    observations: list[SourceObservation] = []
    for o in obs_rows:
        rf = o["raw_fields"]
        rf = rf if isinstance(rf, dict) else json.loads(rf)
        observations.append(
            SourceObservation(
                site=Site(o["site"]),
                source_job_id=o["source_job_id"],
                url=o["url"],
                observed_at=o["first_observed_at"],
                raw_title=o["raw_title"],
                fields=RawJobFields(**rf),
            )
        )
    if not observations:
        return

    # 2. rebuild the survivor from the merged observation set (keeps the survivor's id).
    #    upsert_jobs re-points EVERY observation (losers' included) to the survivor via its
    #    (site, source_job_id) conflict key — so the later loser delete cascades nothing.
    merged = DedupEngine()._build(survivor_id, observations)
    await PostgresJobStore().upsert_jobs([merged])

    # 3. re-point applications (respect the (user, job) unique constraint), drop the loser rows,
    #    re-point feedback, force a re-embed of the survivor, then delete the loser jobs.
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "UPDATE applications SET canonical_job_id = :sid "
                "WHERE canonical_job_id = ANY(:losers) "
                "AND user_id NOT IN (SELECT user_id FROM applications WHERE canonical_job_id = :sid)"
            ),
            {"sid": survivor_id, "losers": losers},
        )
        await s.execute(
            text("DELETE FROM applications WHERE canonical_job_id = ANY(:losers)"), {"losers": losers}
        )
        await s.execute(
            text("UPDATE search_feedback SET canonical_job_id = :sid WHERE canonical_job_id = ANY(:losers)"),
            {"sid": survivor_id, "losers": losers},
        )
        await s.execute(
            text(
                "UPDATE canonical_jobs SET embedding = NULL, embedding_version = NULL "
                "WHERE canonical_job_id = :sid"
            ),
            {"sid": survivor_id},
        )
        await s.execute(
            text("DELETE FROM canonical_jobs WHERE canonical_job_id = ANY(:losers)"), {"losers": losers}
        )
