"""Retrieval quality with free, correct labels — known-item evaluation.

    cd services/corpus && uv run python ../../scripts/retrieval_eval.py [--n 150]

## The problem this solves

`docs/SEARCH-BENCHMARK.md` could show that the two engines *disagree* but not which ordering was
better, because there was no relevance ground truth. Hand-labelling is the usual answer and is not
practical here: relevance is per-profile, the corpus turns over daily, and any labels would be stale
within a week.

## Known-item retrieval

Instead of labelling, construct queries whose correct answer is known by construction — the standard
[known-item](https://trec.nist.gov/) setup, also how dense-retrieval work generates training and
evaluation data when judgements are unavailable.

Pick a job, build a query from its own distinctive terms, and ask: does *that job* come back? The
label is free and provably correct. Averaged over many jobs this measures whether retrieval can find
a specific known document among ~4k competitors, which is a real and demanding property — and one
that degrades visibly when embeddings, filters, or fusion regress.

## What it does and does not measure

It measures **findability**: given text that describes one job, is that job retrieved and ranked
highly. That covers the embedding, the lexical index, the fusion, and the hard filters together.

It does *not* measure **preference**: whether the ordering suits a particular person. Two jobs can
both be findable while the wrong one is shown first for a given profile, and only human judgement or
click feedback can settle that. So this complements the profile re-ranking rather than validating it.

A second caveat: queries built from a job's own words are easier than real user queries, which are
shorter and use different vocabulary. Treat the absolute numbers as an upper bound and watch the
*trend* across changes — that is what makes this useful as a regression metric.

## Metrics

    MRR      mean of 1/rank of the target job; 1.0 means always first
    Recall@k fraction of targets found within the top k
    Miss     target absent from the returned window entirely
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker
from galaxy.search import service as search_service

# Words that describe every job and so identify none. A query built from these retrieves noise.
STOPWORDS = frozenset("""
a an the and or of for in on at to with by from as is are be will you your we our their this that
role job position opportunity team work working experience years including strong excellent good
ability able help build using across within provide support ensure new other more most who what
company business customer product service solutions technology technologies platform
""".split())

CUTOFFS = (1, 3, 5, 10, 20)


@dataclass
class Outcome:
    job_id: str
    query: str
    rank: int | None  # 1-based; None when the target never appeared
    returned: int


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def found(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.rank is not None]

    def mrr(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum((1.0 / o.rank) if o.rank else 0.0 for o in self.outcomes) / len(self.outcomes)

    def recall_at(self, k: int) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.rank and o.rank <= k) / len(self.outcomes)

    def as_dict(self) -> dict:
        ranks = [o.rank for o in self.found]
        return {
            "queries": len(self.outcomes),
            "mrr": round(self.mrr(), 4),
            "recall": {f"@{k}": round(self.recall_at(k), 4) for k in CUTOFFS},
            "misses": len(self.outcomes) - len(self.found),
            "median_rank_when_found": statistics.median(ranks) if ranks else None,
        }


def build_query(title: str, description: str | None, *, terms: int = 6) -> str:
    """A query from the job's own distinctive words.

    Title first — it is the most identifying text a posting has — then the highest-frequency
    non-stopword terms from the description. Frequency within a single document is a crude
    substitute for TF-IDF, but it is stable and needs no corpus pass, and the purpose is a
    *consistent* query construction rather than an optimal one.
    """
    words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9+#.]{1,}", (title or "").lower())
             if w not in STOPWORDS]
    query = list(dict.fromkeys(words))[:terms]

    if len(query) < terms and description:
        body = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9+#.]{2,}", description.lower())
                if w not in STOPWORDS and w not in query]
        for word, _count in Counter(body).most_common():
            query.append(word)
            if len(query) >= terms:
                break
    return " ".join(query)


async def sample_jobs(limit: int) -> list[tuple[str, str, str | None]]:
    """Random searchable jobs. Only embedded ones, since retrieval filters on embedding_version."""
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT canonical_job_id, title, description_md FROM canonical_jobs "
                    "WHERE status = 'open' AND embedding IS NOT NULL AND length(title) > 8 "
                    "ORDER BY random() LIMIT :n"
                ),
                {"n": limit},
            )
        ).all()
    return [(r[0], r[1], r[2]) for r in rows]


async def evaluate(n: int, window: int) -> Report:
    report = Report()
    for job_id, title, description in await sample_jobs(n):
        query = build_query(title, description)
        if not query:
            continue
        try:
            ranked = await search_service.search(search_term=query, limit=window)
        except Exception as exc:  # noqa: BLE001 — one bad query must not end the run
            print(f"  query failed ({exc}): {query[:60]}", file=sys.stderr)
            continue
        rank = next(
            (i for i, job in enumerate(ranked, start=1) if job.canonical_job_id == job_id), None
        )
        report.outcomes.append(Outcome(job_id, query, rank, len(ranked)))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=100, help="jobs to sample")
    parser.add_argument("--window", type=int, default=20, help="how deep to look for the target")
    parser.add_argument("--json", action="store_true", help="machine-readable output only")
    args = parser.parse_args()

    report = asyncio.run(evaluate(args.n, args.window))
    summary = report.as_dict()

    if args.json:
        print(json.dumps(summary, indent=2))
        return

    print(json.dumps(summary, indent=2))
    worst = sorted(report.outcomes, key=lambda o: (o.rank is not None, o.rank or 0), reverse=True)[:8]
    print("\nhardest cases (target ranked lowest or missed):", file=sys.stderr)
    for o in worst:
        print(f"  rank={o.rank or 'MISS':>4}  {o.query[:70]}", file=sys.stderr)


if __name__ == "__main__":
    main()
