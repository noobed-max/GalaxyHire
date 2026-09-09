"""Head-to-head search comparison (§3), measured on the live corpus.

The brief asked to compare GalaxyJobsAi's search engine against JustHireMe's and adopt the better
one. This script produces the numbers behind [docs/SEARCH-BENCHMARK.md](../docs/SEARCH-BENCHMARK.md),
which records why the answer turned out to be "both, in series".

Run with the corpus service up and a profile saved:

    uv run --directory services/corpus python ../../scripts/search_benchmark.py

Three tasks are measured, because the two engines are only comparable on one of them:

  A. **Retrieval** — find the relevant jobs among ~4k rows. Only one engine can attempt this.
  B. **Explanation** — say *why* a job fits this person. Only the other engine produces one.
  C. **Combined** — does the re-rank actually change what the user sees, or is it decoration?

Task C is the one that decides whether keeping both is justified: if re-ranking barely perturbs
retrieval order, the second engine is dead weight.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.error
import urllib.request

CORPUS = "http://127.0.0.1:8100"
API_KEY = "dev-key"

QUERIES = [
    "platform engineer kubernetes",
    "senior backend engineer python",
    "site reliability engineer terraform",
    "frontend react developer",
    "data engineer",
]

# The profile the ranking engine scores against — a platform engineer, so relevance is judgeable.
PROFILE = {
    "skills": [
        {"n": "Python", "cat": "language"},
        {"n": "Go", "cat": "language"},
        {"n": "Kubernetes", "cat": "tool"},
        {"n": "Terraform", "cat": "tool"},
        {"n": "PostgreSQL", "cat": "tool"},
    ],
    "experience": [
        {
            "role": "Platform Engineer",
            "company": "Acme",
            "period": "2022 - present",
            "description": "Ran 12 EKS clusters with Terraform. Built Go services.",
        }
    ],
    "desired_position": "Platform Engineer",
}

STOPWORDS = {"engineer", "developer", "senior", "the", "and"}


def post(path: str, payload: dict, timeout: float = 120) -> dict:
    req = urllib.request.Request(
        f"{CORPUS}{path}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "x-api-key": API_KEY},
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read())


def terms(query: str) -> list[str]:
    return [w for w in query.lower().split() if len(w) > 2 and w not in STOPWORDS]


def overlap(job: dict, query_terms: list[str]) -> bool:
    """Whether a result literally contains any query term.

    A blunt precision proxy, and deliberately so: it under-credits genuine semantic matches, so
    treat it as a floor rather than a score.
    """
    blob = f"{job.get('title', '')} {job.get('company', '')}".lower()
    return any(t in blob for t in query_terms)


def task_a_retrieval() -> list[dict]:
    """Can the engine find relevant jobs in the corpus at all?"""
    rows = []
    for query in QUERIES:
        qt = terms(query)
        started = time.perf_counter()
        body = post("/search", {"search_term": query, "limit": 20})
        elapsed_ms = (time.perf_counter() - started) * 1000
        results = body.get("results", [])
        hits = sum(1 for r in results[:10] if overlap(r, qt))
        rows.append(
            {
                "query": query,
                "returned": len(results),
                "latency_ms": round(elapsed_ms, 1),
                "keyword_hits_at_10": hits,
                "top": (results[0].get("title") if results else None),
            }
        )
    return rows


def task_b_explanation() -> dict:
    """Does each engine explain a fit, or only score it?"""
    body = post("/search", {"search_term": QUERIES[0], "limit": 3})
    results = body.get("results", [])
    corpus_fields = sorted(results[0].keys()) if results else []

    sys.path.insert(0, "../../apps/api")
    ranking_criteria: list[str] = []
    reason = ""
    try:
        import asyncio

        from ranking.service import create_ranking_service

        lead = {
            "job_id": "b1",
            "title": "Platform Engineer",
            "company": "Acme",
            "url": "https://example.com/1",
            "description": "Run Kubernetes at scale with Go and Terraform. 5+ years.",
        }
        verdict = asyncio.run(create_ranking_service().evaluate_lead(lead, PROFILE, None, False))
        reason = str(verdict.get("reason") or "")
        ranking_criteria = sorted(k for k in verdict if k not in {"score", "reason"})
    except Exception as exc:  # noqa: BLE001 - the report should say what failed, not crash
        reason = f"(ranking engine unavailable: {exc})"

    return {
        "corpus_result_fields": corpus_fields,
        "corpus_explains_per_criterion": "explanation" in corpus_fields,
        "ranking_reason": reason[:300],
        "ranking_verdict_fields": ranking_criteria,
    }


def task_c_combined() -> dict:
    """Does re-ranking change the ordering enough to justify keeping it?"""
    sys.path.insert(0, "../../apps/api")
    import asyncio

    from corpus.service import create_corpus_discovery_service

    async def run() -> dict:
        svc = create_corpus_discovery_service()
        rows = []
        for query in QUERIES:
            retrieval = await svc.search(query=query, limit=20, rerank=False)
            reranked = await svc.search(query=query, profile=PROFILE, limit=20)
            before = [x["job_id"] for x in retrieval.leads]
            after = [x["job_id"] for x in reranked.leads]
            if not before or not after:
                continue
            # How much of the visible page changes: what fraction of the new top 10 was not in the
            # old top 10, plus how far the new leader moved.
            top_before, top_after = set(before[:10]), set(after[:10])
            churn = len(top_after - top_before) / max(1, len(top_after))
            leader_shift = before.index(after[0]) if after[0] in before else None
            scores = [x.get("signal_score", 0) for x in reranked.leads]
            rows.append(
                {
                    "query": query,
                    "top10_churn": round(churn, 2),
                    "new_leader_was_rank": leader_shift,
                    "score_spread": (min(scores), max(scores)) if scores else None,
                }
            )
        return {"per_query": rows}

    return asyncio.run(run())


def main() -> None:
    try:
        post("/search", {"search_term": "smoke", "limit": 1})
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"corpus unreachable at {CORPUS}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    report = {
        "task_a_retrieval": task_a_retrieval(),
        "task_b_explanation": task_b_explanation(),
        "task_c_combined": task_c_combined(),
    }
    print(json.dumps(report, indent=2, default=str))

    a = report["task_a_retrieval"]
    print("\n--- summary ---", file=sys.stderr)
    print(
        f"retrieval: median latency {statistics.median(r['latency_ms'] for r in a):.0f} ms, "
        f"keyword hits@10 {sum(r['keyword_hits_at_10'] for r in a)}/{10 * len(a)}",
        file=sys.stderr,
    )
    churns = [r["top10_churn"] for r in report["task_c_combined"]["per_query"]]
    if churns:
        print(f"re-rank top-10 churn: mean {statistics.mean(churns):.2f}", file=sys.stderr)


if __name__ == "__main__":
    main()
