"""ONNX MiniLM embedder (docs/09) — the drop-in semantic upgrade over the hashing default.

Uses fastembed (bundled ONNX runtime + tokenizer) to run `all-MiniLM-L6-v2`, which returns
384-d L2-normalized sentence embeddings. Imported lazily by `embedder.get_embedder()`; if
fastembed isn't installed or the model can't be fetched, the import fails and the factory falls
back to the offline HashingEmbedder.

Enable with:  uv sync --extra embed   +   EMBEDDING_VERSION=minilm-l6-v2
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class OnnxEmbedder:
    version = "minilm-l6-v2"
    dim = 384

    def __init__(self) -> None:
        from fastembed import TextEmbedding  # raises if the extra isn't installed

        # constructs (and on first run downloads) the model; raises if unavailable
        self._model = TextEmbedding(model_name=_MODEL_NAME)
        log.info("embedder.onnx_loaded", model=_MODEL_NAME)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # fastembed yields numpy float arrays, already mean-pooled + L2-normalized
        return [vec.tolist() for vec in self._model.embed(texts)]
