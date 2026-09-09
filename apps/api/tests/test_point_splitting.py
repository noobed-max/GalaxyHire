# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vasudev Siddh and vasu-devs
"""Point-level extraction: blob splitting, stable point ids, and the
ensure_points read-path guarantee (every exp/project row carries selectable
[{id, text}] children derived from its description)."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from profile.ingest_parse import (  # noqa: E402
    _parse_local,
    ensure_points,
    point_id,
    points_with_ids,
    split_points,
)
from profile.ingest_documents import preprocess_resume_text  # noqa: E402


def test_split_points_newline_bullets_lossless():
    blob = (
        "Built a custom object store optimized for ML workflows using Rust.\n"
        "Implemented the Needle-in-a-Haystack architecture, 10x faster than legacy.\n"
        "Benchmarked against Minio using Proxmox, Locust, and Netdata."
    )
    points = split_points(blob)
    assert points == [
        "Built a custom object store optimized for ML workflows using Rust.",
        "Implemented the Needle-in-a-Haystack architecture, 10x faster than legacy.",
        "Benchmarked against Minio using Proxmox, Locust, and Netdata.",
    ]
    # idempotent: re-splitting the joined points yields the same points
    assert split_points("\n".join(points)) == points


def test_split_points_prose_sentence_fallback_and_guards():
    prose = (
        "Engineered a distributed infrastructure on k3s with Longhorn storage. "
        "Sustained 150k req/s at sub-25ms P99. x"  # too-short tail dropped
    )
    points = split_points(prose)
    assert len(points) == 2
    assert all(len(p) >= 16 for p in points)
    # noise + markdown + dedupe
    noisy = "- Built the parser\n- Built the parser\n- view certificate"
    assert split_points(noisy) == ["Built the parser"]
    # a genuine fragment blob is dropped, but a real one-line description survives
    assert split_points("x") == []
    assert split_points("Raft KV store.") == ["Raft KV store."]


def test_split_points_joins_pdf_wraps_but_not_adjacent_points_or_headings():
    wrapped = (
        "• Built the distributed backend on a 4-node cluster and\n"
        "  Garage S3 serving media streams.\n"
        "• Wrote the feed service to hold 150k req/s at sub-25ms P99.\n"
        "• Built hybrid search tuned for\n"
        "  retrieval precision.\n"
        "Personal Projects\n"
        "Moon-Base: Object store"
    )
    assert split_points(wrapped) == [
        "Built the distributed backend on a 4-node cluster and Garage S3 serving media streams.",
        "Wrote the feed service to hold 150k req/s at sub-25ms P99.",
        "Built hybrid search tuned for retrieval precision.",
    ]

    # Short, legitimate marked points remain selectable, while a heading and
    # an entity row do not become description points.
    assert split_points("• Led QA.\n• Go.") == ["Led QA.", "Go."]
    assert split_points("Personal Projects") == []
    # Post-LLM output can omit source markers; only the same obvious fragment
    # cues are joined, while a role/entity row still resets the stream.
    assert split_points("Built the backend and\nGarage S3 serving media streams.") == [
        "Built the backend and Garage S3 serving media streams."
    ]
    assert split_points("Backend Engineer | Acme\nbuilt the API.") == ["built the API."]


def test_sanitized_resume_fixture_has_semantic_experience_counts():
    fixture = preprocess_resume_text(
        """Experience
Intern - Acme 2024 - Present
• Built the backend on a cluster and
  Garage S3 serving media streams.
• Wrote the feed service to hold 150k req/s at sub-25ms P99.
• Built hybrid search tuned for
  retrieval precision.
• Hardened the edge with WAF and
  JWT validation.
Software Engineering Intern - Labs 2023 - 2024
• Built an object store in Rust where reads are small rather
  than large.
• Measured 10x the throughput of the prior system.
• Drove upload latency into single-digit milliseconds.
Personal Projects
Moon-Base: Object Store
• Built the storage engine.
"""
    )
    profile = _parse_local(fixture)
    by_company = {entry.co: entry for entry in profile.exp}
    assert len(by_company["Acme"].points) == 4
    assert len(by_company["Labs"].points) == 3
    assert "Garage S3 serving media streams" in by_company["Acme"].points[0]
    assert "rather than large" in by_company["Labs"].points[0]
    assert all("Personal Projects" not in point for entry in profile.exp for point in entry.points)




def test_contact_line_does_not_absorb_candidate_name():
    from profile.ingest_parse import _parse_resume_heuristic

    profile = _parse_resume_heuristic(
        "Alex Example\n"
        "alex@example.test | github.com/alex\n\n"
        "Skills\nPython, FastAPI\n"
    )
    assert profile.n == "Alex Example"


def test_point_id_stability_and_sensitivity():
    a = point_id("parent1", "Built the parser in Rust")
    assert a == point_id("parent1", "  built   the parser in rust ")  # normalization
    assert a != point_id("parent2", "Built the parser in Rust")       # parent-scoped
    assert a != point_id("parent1", "Built the parser in Go")         # text-scoped


def test_points_with_ids_prefers_existing_ids_for_same_text():
    parent = "p1"
    first = points_with_ids(parent, "Built X\nBuilt Y")
    again = points_with_ids(parent, "Built X\nBuilt Y", existing=first)
    assert [p["id"] for p in again] == [p["id"] for p in first]
    # edited text -> new id; untouched sibling keeps its id
    edited = points_with_ids(parent, "Built X now better\nBuilt Y", existing=first)
    assert edited[0]["id"] != first[0]["id"]
    assert edited[1]["id"] == first[1]["id"]


def test_ensure_points_derives_children_on_read():
    profile = {
        "n": "Test",
        "exp": [{
            "id": "e1", "role": "Intern", "co": "Acme", "period": "2024",
            "d": "Built the ingest pipeline.\nCut latency by 40 percent.",
        }],
        "projects": [{
            "id": "p1", "title": "RoQ", "stack": ["Go"], "repo": "",
            "impact": "Raft consensus KV store across a 5-node cluster.",
        }],
    }
    out = ensure_points(profile)
    e = out["exp"][0]
    assert [p["text"] for p in e["points"]] == [
        "Built the ingest pipeline.",
        "Cut latency by 40 percent.",
    ]
    assert all(p["id"] == point_id("e1", p["text"]) for p in e["points"])
    assert len(out["projects"][0]["points"]) == 1
    # rows that already carry points are left untouched
    out["exp"][0]["points"] = [{"id": "keep", "text": "kept"}]
    assert ensure_points(out)["exp"][0]["points"] == [{"id": "keep", "text": "kept"}]


def test_snapshot_builder_attaches_points():
    # _profile_snapshot_from_import must emit points children for exp/projects.
    from profile.service import _profile_snapshot_from_import

    data = {
        "candidate": {"name": "T", "summary": "s"},
        "experience": [{"role": "Intern", "company": "Acme", "period": "2024",
                        "description": "Built X.\nBuilt Y."}],
        "projects": [{"title": "RoQ", "stack": "Go", "repo": "", "impact": "Raft KV store."}],
    }
    snap = _profile_snapshot_from_import(data, None)
    e = snap["exp"][0]
    assert [p["text"] for p in e["points"]] == ["Built X.", "Built Y."]
    assert all(p["id"] == point_id(e["id"], p["text"]) for p in e["points"])
    assert snap["projects"][0]["points"][0]["text"] == "Raft KV store."
    # re-import with existing snapshot: point ids stable
    again = _profile_snapshot_from_import(data, snap)
    assert [p["id"] for p in again["exp"][0]["points"]] == [p["id"] for p in e["points"]]
