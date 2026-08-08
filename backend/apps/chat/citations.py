"""Block-relative citation offsets → document coordinates.

The whole feature is one addition:

    doc_char_start = block.base_offset + citation.start_char_index

and it is exact only because two things hold. The API returns `char_location`
offsets relative to the content block, and the block's text is byte-identical to
a slice of `Document.normalized_text` — which is what the M3 offset invariant
guarantees and what `context.py` is careful not to break.

So the arithmetic is trivial and the *verification* is the interesting part.
Every resolved citation is checked against the document text before it is
stored, and one that does not match is dropped rather than shown. A citation
mark that highlights the wrong span is worse than a missing one: the missing
one looks like the model didn't cite, the wrong one looks like the system is
lying about its evidence, and the user has no way to tell which.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from apps.documents.models import Document
from llm.types import CitationDelta, DocumentBlock

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ResolvedCitation:
    index: int
    document_id: str
    chunk_id: str | None
    doc_char_start: int
    doc_char_end: int
    cited_text: str
    answer_char: int


class CitationResolver:
    """Resolves citation deltas for one message, in arrival order."""

    def __init__(self, blocks: list[DocumentBlock], documents: dict[str, Document]) -> None:
        self._blocks = blocks
        self._documents = documents
        self._next_index = 1
        # Same span cited twice gets the same `[n]`. Models do re-cite, and two
        # marks pointing at one span reads as two pieces of evidence.
        self._seen: dict[tuple[str, int, int], ResolvedCitation] = {}
        self.dropped = 0

    def resolve(self, delta: CitationDelta) -> ResolvedCitation | None:
        if not 0 <= delta.document_index < len(self._blocks):
            # A `document_index` outside the blocks we sent means our idea of
            # the request and the API's disagree. Loud, because it invalidates
            # every other citation in the response too.
            log.warning(
                "citation_block_out_of_range",
                document_index=delta.document_index,
                block_count=len(self._blocks),
            )
            self.dropped += 1
            return None

        block = self._blocks[delta.document_index]
        start = block.base_offset + delta.start_char_index
        end = block.base_offset + delta.end_char_index

        if end <= start:
            self.dropped += 1
            return None

        document = self._documents.get(block.document_id)
        if document is None:
            self.dropped += 1
            return None

        text = document.normalized_text
        if end > len(text) or text[start:end] != delta.cited_text:
            # The arithmetic said one thing and the document says another. This
            # is exactly the failure the offset contract exists to prevent, so
            # it is logged with the offsets — never with the text — and the
            # citation is discarded.
            log.warning(
                "citation_offset_mismatch",
                document_id=block.document_id,
                chunk_id=block.chunk_id,
                base_offset=block.base_offset,
                start=start,
                end=end,
                doc_len=len(text),
                cited_len=len(delta.cited_text),
            )
            self.dropped += 1
            return None

        key = (block.document_id, start, end)
        if existing := self._seen.get(key):
            return existing

        resolved = ResolvedCitation(
            index=self._next_index,
            document_id=block.document_id,
            chunk_id=block.chunk_id,
            doc_char_start=start,
            doc_char_end=end,
            cited_text=delta.cited_text,
            answer_char=delta.answer_char,
        )
        self._seen[key] = resolved
        self._next_index += 1
        return resolved


def load_documents(blocks: list[DocumentBlock]) -> dict[str, Document]:
    """Every document referenced by a block, in one query.

    Fetched up front rather than per citation: citations arrive mid-stream, and
    a database round-trip between text deltas is latency the user watches
    happen.
    """
    ids = {block.document_id for block in blocks}
    return {str(d.id): d for d in Document.objects.filter(id__in=ids)}
