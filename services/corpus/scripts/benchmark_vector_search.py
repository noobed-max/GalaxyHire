#!/usr/bin/env python3
"""Measure filtered HNSW recall and latency against exact pgvector search.

This is diagnostic only: it never changes indexes or stored data. Run from services/corpus:

    uv run python scripts/benchmark_vector_search.py --queries 25 --k 20
    uv run python scripts/benchmark_vector_search.py --seniority junior
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass

from sqlalchemy import text

from galaxy.common.config import get_settings
from galaxy.db.engine import get_sessionmaker


@dataclass(frozen=True)
class QueryMeasurement:
    job_id: str
    recall: float
    approximate_ms: float
    exact_ms: float


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


async def _neighbors(
    *,
    qvec: str,
    exclude_id: str,
    version: str,
    seniority: str | None,
    k: int,
    exact: bool,
    ef_search: int,
) -> tuple[list[str], float]:
    sm = get_sessionmaker()
    async with sm() as session, session.begin():
        if exact:
            # HNSW uses a normal index scan. Turning every index-scan form off gives us the
            # sequential exact result to use as ground truth for this diagnostic transaction.
            await session.execute(text("SET LOCAL enable_indexscan = off"))
            await session.execute(text("SET LOCAL enable_indexonlyscan = off"))
            await session.execute(text("SET LOCAL enable_bitmapscan = off"))
        else:
            await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            await session.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))
        # Measure the neighbor query itself, not pool startup or the diagnostic SET commands.
        started = time.perf_counter()
        rows = await session.execute(
            text(
                "SELECT canonical_job_id FROM canonical_jobs "
                "WHERE status = 'open' AND embedding_version = :version "
                "AND canonical_job_id <> :exclude_id "
                "AND (CAST(:seniority AS text) IS NULL "
                "OR seniority = CAST(:seniority AS text)) "
                "ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :k"
            ),
            {
                "version": version,
                "exclude_id": exclude_id,
                "seniority": seniority,
                "qvec": qvec,
                "k": k,
            },
        )
        ids = [row[0] for row in rows.all()]
        elapsed_ms = (time.perf_counter() - started) * 1000
    return ids, elapsed_ms


async def benchmark(
    *,
    queries: int,
    k: int,
    ef_search: int,
    seniority: str | None,
) -> dict:
    version = get_settings().embedding_version
    sm = get_sessionmaker()
    async with sm() as session:
        samples = (
            await session.execute(
                text(
                    "SELECT canonical_job_id, embedding::text FROM canonical_jobs "
                    "WHERE status = 'open' AND embedding_version = :version "
                    "AND (CAST(:seniority AS text) IS NULL "
                    "OR seniority = CAST(:seniority AS text)) "
                    "ORDER BY random() LIMIT :queries"
                ),
                {"version": version, "seniority": seniority, "queries": queries},
            )
        ).all()
    if not samples:
        raise RuntimeError(
            f"no open embeddings for version {version!r}"
            + (f" and seniority {seniority!r}" if seniority else "")
        )

    measurements: list[QueryMeasurement] = []
    for job_id, qvec in samples:
        approximate, approximate_ms = await _neighbors(
            qvec=qvec,
            exclude_id=job_id,
            version=version,
            seniority=seniority,
            k=k,
            exact=False,
            ef_search=ef_search,
        )
        exact, exact_ms = await _neighbors(
            qvec=qvec,
            exclude_id=job_id,
            version=version,
            seniority=seniority,
            k=k,
            exact=True,
            ef_search=ef_search,
        )
        denominator = len(exact)
        recall = len(set(approximate) & set(exact)) / denominator if denominator else 1.0
        measurements.append(
            QueryMeasurement(
                job_id=job_id,
                recall=round(recall, 4),
                approximate_ms=round(approximate_ms, 2),
                exact_ms=round(exact_ms, 2),
            )
        )

    recalls = [row.recall for row in measurements]
    approximate_times = [row.approximate_ms for row in measurements]
    exact_times = [row.exact_ms for row in measurements]
    return {
        "embedding_version": version,
        "filter": {"seniority": seniority},
        "queries": len(measurements),
        "k": k,
        "ef_search": ef_search,
        "recall": {
            "mean": round(statistics.fmean(recalls), 4),
            "minimum": round(min(recalls), 4),
        },
        "latency_ms": {
            "approximate_p50": _percentile(approximate_times, 0.50),
            "approximate_p95": _percentile(approximate_times, 0.95),
            "exact_p50": _percentile(exact_times, 0.50),
            "exact_p95": _percentile(exact_times, 0.95),
        },
        "measurements": [asdict(row) for row in measurements],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=int, default=25)
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument("--ef-search", type=int, default=120)
    parser.add_argument("--seniority", default=None)
    args = parser.parse_args()
    queries = max(1, min(200, args.queries))
    k = max(1, min(100, args.k))
    ef_search = max(k, min(1000, args.ef_search))
    result = asyncio.run(
        benchmark(queries=queries, k=k, ef_search=ef_search, seniority=args.seniority)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
