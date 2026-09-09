#!/usr/bin/env python3
"""Export real corpus results for human judgment, then score the completed judgments.

Usage from services/corpus:

    uv run python scripts/relevance_eval.py export
    # Fill relevance (0..3) and hard_violation (true/false) in evals/judgments.jsonl.
    uv run python scripts/relevance_eval.py score
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from galaxy.search import service
from galaxy.search.eval import Judgment, summarize_judgments

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTENTS = ROOT / "evals" / "search_intents.jsonl"
DEFAULT_JUDGMENTS = ROOT / "evals" / "judgments.jsonl"


def _jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows


async def export(intents_path: Path, output_path: Path, force: bool) -> None:
    if output_path.exists() and not force:
        raise FileExistsError(
            f"{output_path} already exists; preserve its labels or pass --force to replace it"
        )
    lines: list[str] = []
    for intent in _jsonl(intents_path):
        name = str(intent["name"])
        args = dict(intent["args"])
        ranked = await service.search(**args)
        for rank, job in enumerate(ranked, 1):
            row = {
                "query": name,
                "args": args,
                "rank": rank,
                "job_id": job.canonical_job_id,
                "title": job.title,
                "company": job.company,
                "seniority": job.seniority,
                "location": job.location,
                "relevance": None,
                "hard_violation": None,
                "notes": "",
            }
            lines.append(json.dumps(row, ensure_ascii=False))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"Exported {len(lines)} results to {output_path}")


def score(judgments_path: Path) -> None:
    rows = _jsonl(judgments_path)
    unlabeled = [
        index
        for index, row in enumerate(rows, 1)
        if row.get("relevance") is None or row.get("hard_violation") is None
    ]
    if unlabeled:
        sample = ", ".join(str(index) for index in unlabeled[:10])
        raise ValueError(
            f"{len(unlabeled)} rows are unlabeled (lines {sample}); fill every judgment first"
        )
    invalid = [
        index
        for index, row in enumerate(rows, 1)
        if type(row.get("relevance")) is not int  # noqa: E721 - bool must not count as an int label
        or not isinstance(row.get("hard_violation"), bool)
    ]
    if invalid:
        sample = ", ".join(str(index) for index in invalid[:10])
        raise ValueError(
            "relevance must be an integer and hard_violation must be a JSON boolean "
            f"(invalid lines {sample})"
        )
    judgments = [
        Judgment(
            query=str(row["query"]),
            job_id=str(row["job_id"]),
            rank=int(row["rank"]),
            relevance=int(row["relevance"]),
            hard_violation=bool(row["hard_violation"]),
        )
        for row in rows
    ]
    print(json.dumps(asdict(summarize_judgments(judgments)), indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export_parser = sub.add_parser("export", help="run intents and create an unlabeled result pool")
    export_parser.add_argument("--intents", type=Path, default=DEFAULT_INTENTS)
    export_parser.add_argument("--output", type=Path, default=DEFAULT_JUDGMENTS)
    export_parser.add_argument("--force", action="store_true")
    score_parser = sub.add_parser("score", help="score a completely labeled result pool")
    score_parser.add_argument("--judgments", type=Path, default=DEFAULT_JUDGMENTS)
    args = parser.parse_args()
    if args.command == "export":
        asyncio.run(export(args.intents, args.output, args.force))
    else:
        score(args.judgments)


if __name__ == "__main__":
    main()
