"""ONNX MiniLM embedder test — real semantic quality (docs/09).

Guarded: runs only with GALAXY_TEST_ONNX=1 (needs the `embed` extra + a model download), so the
default suite stays fast and offline.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("GALAXY_TEST_ONNX") != "1", reason="set GALAXY_TEST_ONNX=1 (needs the embed extra)"
)


def test_onnx_embedder_semantic_quality():
    from galaxy.search.embedder_onnx import OnnxEmbedder

    e = OnnxEmbedder()
    assert e.version == "minilm-l6-v2"
    assert e.dim == 384

    v = e.embed(
        [
            "senior python backend engineer building APIs",
            "python software developer for web services",
            "marketing brand manager for consumer goods",
        ]
    )
    assert all(len(x) == 384 for x in v)
    norm = sum(x * x for x in v[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-3  # L2-normalized

    def cos(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True))

    # related roles are far closer than an unrelated one
    assert cos(v[0], v[1]) > 0.4
    assert cos(v[0], v[1]) > cos(v[0], v[2]) + 0.3
