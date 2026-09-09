"""Embeddings (docs/03 §6, docs/09).

Two implementations behind one protocol, each carrying a `version` stamped onto every stored
vector (`embedding_version`). Model vectors and fallback vectors are NEVER comparable, so every
vector query filters to a single version (enforced in search.retrieval).

- HashingEmbedder — deterministic, offline, zero-dependency. The always-available default; good
  enough to exercise the ranking machinery. version = "hash-v1".
- OnnxEmbedder — local ONNX `all-MiniLM-L6-v2`; activates only if the model files are present
  (opt-in extra `galaxy[embed]`). version = "minilm-l6-v2". A drop-in upgrade: re-embed +
  reindex, gated on the golden set (docs/04 §6).
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import structlog

from galaxy.common.config import get_settings

log = structlog.get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")


class Embedder(Protocol):
    version: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


class HashingEmbedder:
    """Feature-hashing embedder: unigrams + bigrams → signed buckets, L2-normalized.

    Deterministic and offline. Cosine similarity rises with shared vocabulary, which is enough
    to drive and test the hybrid ranker; swap in `OnnxEmbedder` for semantic quality.
    """

    version = "hash-v1"

    def __init__(self, dim: int = 384):
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        toks = _tokens(text)
        grams = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
        for gram in grams:
            h = int.from_bytes(hashlib.blake2b(gram.encode(), digest_size=8).digest(), "big")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


def _try_onnx() -> Embedder | None:
    """Load the ONNX MiniLM embedder if its files + deps are present; else None."""
    try:
        from galaxy.search.embedder_onnx import OnnxEmbedder  # optional module

        return OnnxEmbedder()
    except Exception as exc:  # noqa: BLE001 — any missing dep/model → fall back
        log.info("embedder.onnx_unavailable", reason=str(exc))
        return None


_active: Embedder | None = None


def get_embedder() -> Embedder:
    """Return the active embedder (ONNX if available, else hashing). Cached per process."""
    global _active
    if _active is None:
        settings = get_settings()
        onnx = _try_onnx() if settings.embedding_version.startswith("minilm") else None
        _active = onnx or HashingEmbedder(dim=settings.embedding_dim)
        log.info("embedder.active", version=_active.version, dim=_active.dim)
    return _active


def to_pgvector(vec: list[float]) -> str:
    """Format a vector as a pgvector literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def embedding_text(title: str, description: str | None, keywords: list[str] | None) -> str:
    """The text we embed for a job (docs/03 §6): title + description + keyword signal."""
    parts = [title or ""]
    if description:
        parts.append(description[:4000])
    if keywords:
        parts.append(" ".join(keywords))
    return "\n".join(parts)
