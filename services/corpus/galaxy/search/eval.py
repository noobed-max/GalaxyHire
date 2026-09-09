"""Ranking evaluation harness (docs/04 §6).

nDCG@10 / recall@50 over a golden set of hand-labeled (query, job, relevance) tuples. A weight,
embedding, or fusion change must not regress nDCG@10 — this is the gate, wired into CI, that
keeps ranking changes honest ("not vibes"). The golden set starts small (~20 tuples on day one)
and grows from real user feedback.

`summarize_judgments` also scores exported real-corpus result pages. Those judgments include hard
constraint violations separately from graded relevance: a perfect-looking senior job is still a
search failure when the request explicitly excluded senior roles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class GoldenQuery:
    """One labeled query: the search args + judged relevance per job id (0..3)."""

    name: str
    args: dict  # kwargs for service.search
    relevance: dict[str, int] = field(default_factory=dict)  # canonical_job_id -> 0..3


def dcg_at_k(gains: list[float], k: int) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains[:k]))


def ndcg_at_k(ranked_ids: list[str], relevance: dict[str, int], k: int = 10) -> float:
    gains = [relevance.get(jid, 0) for jid in ranked_ids]
    ideal = sorted(relevance.values(), reverse=True)
    idcg = dcg_at_k([float(g) for g in ideal], k)
    if idcg == 0:
        return 0.0
    return dcg_at_k([float(g) for g in gains], k) / idcg


def recall_at_k(ranked_ids: list[str], relevance: dict[str, int], k: int = 50) -> float:
    relevant = {jid for jid, r in relevance.items() if r > 0}
    if not relevant:
        return 0.0
    retrieved = set(ranked_ids[:k])
    return len(relevant & retrieved) / len(relevant)


@dataclass
class EvalResult:
    ndcg_at_10: float
    recall_at_50: float
    per_query: dict[str, dict]


@dataclass(frozen=True)
class Judgment:
    """One human judgment from a displayed result page.

    Relevance uses the conventional 0..3 scale. `hard_violation` is independent because constraint
    failures should never be hidden inside an average relevance score.
    """

    query: str
    job_id: str
    rank: int
    relevance: int
    hard_violation: bool = False


@dataclass(frozen=True)
class JudgmentSummary:
    queries: int
    judgments: int
    ndcg_at_10: float
    precision_at_10: float
    hard_violation_rate: float
    per_query: dict[str, dict]


async def evaluate(golden: list[GoldenQuery]) -> EvalResult:
    """Run each golden query through the live search service and score it."""
    from galaxy.search import service

    per_query: dict[str, dict] = {}
    ndcgs, recalls = [], []
    for g in golden:
        ranked = await service.search(**g.args)
        ids = [j.canonical_job_id for j in ranked]
        n = ndcg_at_k(ids, g.relevance, 10)
        r = recall_at_k(ids, g.relevance, 50)
        per_query[g.name] = {"ndcg@10": round(n, 4), "recall@50": round(r, 4), "n": len(ids)}
        ndcgs.append(n)
        recalls.append(r)
    return EvalResult(
        ndcg_at_10=round(sum(ndcgs) / len(ndcgs), 4) if ndcgs else 0.0,
        recall_at_50=round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        per_query=per_query,
    )


def summarize_judgments(judgments: list[Judgment], k: int = 10) -> JudgmentSummary:
    """Score fully judged result pages, grouped by query name.

    Precision treats grades 1..3 as relevant. nDCG retains the difference between "possibly
    useful" and "ideal". Hard-violation rate is reported independently and should be exactly zero.
    """
    grouped: dict[str, list[Judgment]] = {}
    for judgment in judgments:
        if not 0 <= judgment.relevance <= 3:
            raise ValueError("relevance must be an integer from 0 to 3")
        if judgment.rank < 1:
            raise ValueError("rank must be at least 1")
        grouped.setdefault(judgment.query, []).append(judgment)

    per_query: dict[str, dict] = {}
    ndcgs: list[float] = []
    precisions: list[float] = []
    violations = 0
    for query, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: row.rank)
        ids = [row.job_id for row in ordered]
        relevance = {row.job_id: row.relevance for row in ordered}
        n = ndcg_at_k(ids, relevance, k)
        top = ordered[:k]
        p = sum(row.relevance > 0 for row in top) / len(top) if top else 0.0
        query_violations = sum(row.hard_violation for row in ordered)
        violations += query_violations
        ndcgs.append(n)
        precisions.append(p)
        per_query[query] = {
            f"ndcg@{k}": round(n, 4),
            f"precision@{k}": round(p, 4),
            "hard_violations": query_violations,
            "judgments": len(ordered),
        }

    count = len(judgments)
    return JudgmentSummary(
        queries=len(grouped),
        judgments=count,
        ndcg_at_10=round(sum(ndcgs) / len(ndcgs), 4) if ndcgs else 0.0,
        precision_at_10=round(sum(precisions) / len(precisions), 4) if precisions else 0.0,
        hard_violation_rate=round(violations / count, 4) if count else 0.0,
        per_query=per_query,
    )
