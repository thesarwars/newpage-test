"""Stable labels for fixture chunks.

The golden set refers to chunks by **tag**, never by id. Chunk ids are UUIDs
minted at ingest, so an id-based golden set is invalidated by every re-ingest —
and re-chunking is exactly when you most need the eval to still work. A tag like
`vertex:requirements` survives a change to chunk sizes, packing or overlap,
which means the eval measures the retrieval change rather than dying of it.

The tag is derived from data that is already stable: which document a chunk came
from, and which section. If a chunker change moves a requirement bullet into a
different section, that *should* fail the eval — the tag being wrong is the
finding.
"""

from __future__ import annotations

from apps.documents.models import Chunk, Document, DocumentKind

# Fixture file stem -> short key used in the golden set.
DOCUMENT_KEYS = {
    "resume": "resume",
    "job_1_northwind": "northwind",
    "job_2_vertex": "vertex",
    "job_3_helio": "helio",
}


def document_key(document: Document) -> str:
    """Short, stable handle for a fixture document."""
    if document.kind == DocumentKind.RESUME:
        return "resume"
    stem = (document.original_filename or "").rsplit(".", 1)[0]
    if key := DOCUMENT_KEYS.get(stem):
        return key
    label = (document.company or document.label or "").strip().lower()
    return label.split()[0] if label else f"job{document.ordinal}"


def tag(chunk: Chunk) -> str:
    """`<document>:<section>` — what the golden set matches on."""
    section = chunk.section.kind if chunk.section else "other"
    return f"{document_key(chunk.document)}:{section}"


def tags_for(chunks: list[Chunk]) -> list[str]:
    return [tag(chunk) for chunk in chunks]
