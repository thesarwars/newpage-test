"""Local ONNX embeddings via fastembed.

The default, and the reason the app runs with no API key at all. Weights are
baked into the image at build time (see backend/Dockerfile), so a cold container
never reaches HuggingFace and the whole pipeline works offline.

Note on what is actually loaded: fastembed serves `BAAI/bge-small-en-v1.5` from
a quantized ONNX port (`qdrant/bge-small-en-v1.5-onnx-q`). Same 384 dimensions,
same tokenizer, smaller and faster on CPU. Worth stating plainly rather than
implying the original float weights are in use.
"""

from __future__ import annotations

import functools
from typing import Any, cast

import structlog
from django.conf import settings

from apps.rag.embeddings.base import QUERY_INSTRUCTION

log = structlog.get_logger(__name__)

BATCH_SIZE = 32


class LocalEmbedder:
    """fastembed + bge-small-en-v1.5, on CPU."""

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or settings.EMBEDDING_MODEL

    @property
    def name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return 384

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Embed chunks. Bare — no instruction prefix (see base.py)."""
        if not texts:
            return []
        vectors = self._model().embed(texts, batch_size=BATCH_SIZE)
        return [cast("list[float]", vector.tolist()) for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        """Embed a query, with the instruction prefix bge-v1.5 expects."""
        prefixed = f"{QUERY_INSTRUCTION}{text}"
        vector = next(iter(self._model().embed([prefixed])))
        return cast("list[float]", vector.tolist())

    # fastembed ships no py.typed marker, so TextEmbedding is Any to mypy either
    # way. Returning Any here and casting at the two call sites keeps the
    # untyped surface to a named boundary rather than letting it leak outward.
    @functools.cache  # noqa: B019 - one embedder instance per process by design
    def _model(self) -> Any:
        """Load lazily and once.

        Not at import time: Django's autoreloader imports settings repeatedly,
        and loading an ONNX session per reload turns a one-second restart into a
        several-second one.
        """
        from fastembed import TextEmbedding

        log.info("embedder_loading", model=self._model_name)
        return TextEmbedding(self._model_name)
