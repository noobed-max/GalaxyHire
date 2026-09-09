"""Dedup engine (docs/03).

Groups SourceObservations by canonical id, merges each group per-field, and builds one
CanonicalJob per logical role. Also reports the over-merge split metric: how many
same-company/title/location base groups the description bucket split into multiple reqs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from galaxy.dedup.canonical import base_group_key, canonical_job_id
from galaxy.dedup.legitimacy import assess_legitimacy
from galaxy.dedup.resolver import MergeResolver, location_value
from galaxy.dedup.simhash import SIMHASH_MERGE_THRESHOLD, hamming, simhash
from galaxy.ingestion.normalize import NORMALIZER_VERSION, norm_company
from galaxy.models.enums import JobStatus
from galaxy.models.job import CanonicalJob, Compensation, Location, SourceObservation


def _cluster_by_description(
    observations: list[SourceObservation], threshold: int = SIMHASH_MERGE_THRESHOLD
) -> list[list[SourceObservation]]:
    """Cluster observations sharing a base key into distinct reqs by description similarity.

    Union-find over pairwise SimHash Hamming distance: descriptions within `threshold` bits are
    the same posting; beyond it, distinct reqs. Empty descriptions all cluster together (no signal
    to split on).
    """
    n = len(observations)
    hashes = [
        simhash(o.fields.description_md or o.fields.description_html or "") for o in observations
    ]
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    for i in range(n):
        for j in range(i + 1, n):
            # two empty descriptions (hash 0) merge; otherwise compare bit distance
            if hamming(hashes[i], hashes[j]) <= threshold:
                union(i, j)

    clusters: dict[int, list[SourceObservation]] = {}
    for idx, obs in enumerate(observations):
        clusters.setdefault(find(idx), []).append(obs)
    return list(clusters.values())


def _cluster_disambiguator(cluster: list[SourceObservation], multi: bool) -> str:
    """Stable per-cluster tag appended to the canonical id.

    Empty when a base group has a single cluster (the common case: real duplicates). When a base
    group splits into multiple reqs, each cluster is tagged by the SimHash of its richest
    description — deterministic for a given set of postings, so re-scraping is stable.
    """
    if not multi:
        return ""
    richest = max(
        cluster, key=lambda o: len(o.fields.description_md or o.fields.description_html or "")
    )
    h = simhash(richest.fields.description_md or richest.fields.description_html or "")
    return f"{h:016x}"


@dataclass
class DedupStats:
    observations_in: int = 0
    canonical_out: int = 0
    base_groups: int = 0
    # base groups that split into >1 canonical id because descriptions diverged (over-merge guard)
    split_groups: int = 0

    @property
    def split_rate(self) -> float:
        return self.split_groups / self.base_groups if self.base_groups else 0.0


@dataclass
class DedupResult:
    jobs: list[CanonicalJob] = field(default_factory=list)
    stats: DedupStats = field(default_factory=DedupStats)


class DedupEngine:
    def __init__(self, resolver: MergeResolver | None = None):
        self.resolver = resolver or MergeResolver()

    def dedup(self, observations: list[SourceObservation]) -> DedupResult:
        # Stage 1: coarse group by company|title|location.
        base_groups: dict[str, list[SourceObservation]] = {}
        for obs in observations:
            base_groups.setdefault(base_group_key(obs.fields), []).append(obs)

        jobs: list[CanonicalJob] = []
        split_groups = 0

        # Stage 2: within each base group, cluster by description similarity (over-merge guard).
        for group in base_groups.values():
            clusters = _cluster_by_description(group)
            multi = len(clusters) > 1
            if multi:
                split_groups += 1
            for cluster in clusters:
                tag = _cluster_disambiguator(cluster, multi)
                cid = canonical_job_id(cluster[0].fields, tag)
                jobs.append(self._build(cid, cluster))

        stats = DedupStats(
            observations_in=len(observations),
            canonical_out=len(jobs),
            base_groups=len(base_groups),
            split_groups=split_groups,
        )
        return DedupResult(jobs=jobs, stats=stats)

    def _build(self, canonical_id: str, group: list[SourceObservation]) -> CanonicalJob:
        fields = self.resolver.merge(group)
        now = datetime.now(UTC)

        first_seen = min(o.observed_at for o in group)
        last_seen = max(o.observed_at for o in group)

        loc: Location = location_value(fields)
        title = fields["title"].value
        company = fields["company"].value
        description = fields["description"].value if "description" in fields else None
        comp: Compensation | None = fields["compensation"].value if "compensation" in fields else None

        def dval(name):
            return fields[name].value if name in fields else None

        legitimacy = assess_legitimacy(group, description, company)

        return CanonicalJob(
            canonical_job_id=canonical_id,
            title=title,
            company=company,
            company_key=norm_company(company),
            location=loc,
            description_md=description,
            primary_url=fields["url"].value,
            compensation=comp,
            seniority=dval("seniority"),
            min_years_experience=dval("min_years_experience"),
            onsite_policy=dval("onsite_policy"),
            clearance_required=dval("clearance_required"),
            jd_keywords=dval("jd_keywords") or [],
            jd_skills=dval("jd_skills") or [],
            sources=list(group),
            fields=fields,
            status=JobStatus.OPEN,
            legitimacy=legitimacy,
            first_seen_at=first_seen,
            last_seen_at=last_seen,
            date_posted=dval("date_posted"),
            normalizer_version=NORMALIZER_VERSION,
            embedding_version=None,  # set by the embedder in Phase 3
            merged_at=now,
        )


