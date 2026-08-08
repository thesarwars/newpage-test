"""Embedder selection.

One process-wide instance, chosen by `EMBEDDING_BACKEND`. The model holds an
ONNX session and loading it is measured in seconds, so this is cached rather
than constructed per request.
"""

from __future__ import annotations

import functools

from django.conf import settings

from apps.rag.embeddings.base import QUERY_INSTRUCTION, Embedder
from apps.rag.embeddings.local_fastembed import LocalEmbedder

__all__ = ["QUERY_INSTRUCTION", "Embedder", "LocalEmbedder", "get_embedder"]


@functools.cache
def get_embedder() -> Embedder:
    backend = settings.EMBEDDING_BACKEND
    if backend == "local":
        return LocalEmbedder()
    # `voyage` lands here when a hosted embedder is wanted — one class, plus a
    # documented reindex, because the vectors are not interchangeable.
    raise ValueError(
        f"Unknown EMBEDDING_BACKEND {backend!r}. Supported: 'local'. "
        "A hosted embedder is a new Embedder implementation plus a full reindex."
    )
