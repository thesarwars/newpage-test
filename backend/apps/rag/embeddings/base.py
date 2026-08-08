"""The embedding seam.

Two implementations sit behind this: local ONNX (the default, no key) and a
deterministic fake for tests that must not load a model. A hosted embedder —
Voyage, OpenAI — is a third, and the protocol is what makes it a class rather
than a refactor. Swapping it also requires a full reindex, which is why
`Chunk.embedding` is dimensioned in the schema (see apps/documents/models.py).

**Query/passage asymmetry is part of the contract, not an implementation
detail.** bge-v1.5 is trained asymmetrically: queries must carry an instruction
prefix, passages must not. Getting it backwards costs roughly 4 points of
Recall@10 and raises no error anywhere — so it lives here, in the interface,
with a test in both directions.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# The instruction bge-v1.5 was trained to see on the query side, and only there.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


@runtime_checkable
class Embedder(Protocol):
    """Turns text into vectors. The only thing retrieval knows about a model."""

    @property
    def name(self) -> str:
        """Model identifier, stored on Document.embedding_model.

        Persisted per document so a corpus embedded with one model is
        distinguishable from one embedded with another — the alternative is
        silently comparing vectors from different spaces.
        """
        ...

    @property
    def dimensions(self) -> int: ...

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Embed document chunks. No instruction prefix."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query. Instruction prefix applied."""
        ...
