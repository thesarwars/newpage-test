"""Block-relative offsets → document coordinates.

The arithmetic is one addition. These tests are about everything that makes the
addition *correct*, and about what happens when it isn't — because a citation
mark on the wrong span is worse than no mark at all. A missing mark looks like
the model didn't cite; a wrong one looks like the system is lying about its
evidence, and the user has no way to tell the difference.
"""

from __future__ import annotations

import pytest

from apps.chat.citations import CitationResolver, load_documents
from apps.core.models import Session
from apps.documents.ingest import ingest_text
from apps.documents.models import Chunk, Document, DocumentKind
from llm.types import CitationDelta, DocumentBlock

RESUME = """PROFESSIONAL SUMMARY
Backend engineer with eight years building payment systems.

EXPERIENCE
Senior Backend Engineer — Meridian Logistics (2022 to Present)
Reduced p99 latency from 1.4s to 380ms across the dispatch API.
Owned the migration from a monolith to six Go services.

SKILLS
Go, Python, PostgreSQL, Kafka, Terraform, AWS
"""


@pytest.fixture
def resume(session: Session) -> Document:
    result = ingest_text(session=session, kind=DocumentKind.RESUME, text=RESUME, label="Résumé")
    return result.document


def _cite(block_index: int, start: int, end: int, text: str) -> CitationDelta:
    return CitationDelta(
        document_index=block_index,
        start_char_index=start,
        end_char_index=end,
        cited_text=text,
        answer_char=0,
    )


class TestOffsetMapping:
    def test_whole_document_block_needs_no_arithmetic(self, resume: Document) -> None:
        """base_offset 0 is why the résumé goes in whole. This is that payoff."""
        text = resume.normalized_text
        start = text.index("Reduced p99")
        end = start + len("Reduced p99 latency from 1.4s to 380ms")

        block = DocumentBlock(title="Résumé", text=text, document_id=str(resume.id), base_offset=0)
        resolver = CitationResolver([block], {str(resume.id): resume})

        resolved = resolver.resolve(_cite(0, start, end, text[start:end]))

        assert resolved is not None
        assert (resolved.doc_char_start, resolved.doc_char_end) == (start, end)

    def test_chunk_block_offsets_shift_by_char_start(self, resume: Document) -> None:
        """The M3 invariant, cashed in.

        `normalized_text[char_start:char_end] == text` is what makes
        `base_offset + block_offset` exact. If that invariant ever breaks, this
        is the test that says so.
        """
        chunk = Chunk.objects.filter(document=resume).order_by("ordinal").last()
        assert chunk is not None
        assert resume.normalized_text[chunk.char_start : chunk.char_end] == chunk.text

        block = DocumentBlock(
            title="Résumé — Skills",
            text=chunk.text,
            document_id=str(resume.id),
            base_offset=chunk.char_start,
            chunk_id=str(chunk.id),
        )
        resolver = CitationResolver([block], {str(resume.id): resume})

        local_start = chunk.text.index("Go")
        local_end = local_start + 2
        resolved = resolver.resolve(_cite(0, local_start, local_end, "Go"))

        assert resolved is not None
        assert resolved.doc_char_start == chunk.char_start + local_start
        assert resume.normalized_text[resolved.doc_char_start : resolved.doc_char_end] == "Go"
        assert resolved.chunk_id == str(chunk.id)


class TestRejection:
    def test_offset_that_does_not_match_the_document_is_dropped(self, resume: Document) -> None:
        """A wrong highlight is worse than a missing one. Drop, don't guess."""
        block = DocumentBlock(
            title="Résumé", text=resume.normalized_text, document_id=str(resume.id)
        )
        resolver = CitationResolver([block], {str(resume.id): resume})

        resolved = resolver.resolve(_cite(0, 10, 30, "text that is not there"))

        assert resolved is None
        assert resolver.dropped == 1

    def test_offset_past_the_end_of_the_document_is_dropped(self, resume: Document) -> None:
        block = DocumentBlock(
            title="Résumé", text=resume.normalized_text, document_id=str(resume.id)
        )
        resolver = CitationResolver([block], {str(resume.id): resume})

        assert resolver.resolve(_cite(0, 5, 10_000, "whatever")) is None
        assert resolver.dropped == 1

    def test_document_index_outside_the_blocks_we_sent_is_dropped(self, resume: Document) -> None:
        block = DocumentBlock(
            title="Résumé", text=resume.normalized_text, document_id=str(resume.id)
        )
        resolver = CitationResolver([block], {str(resume.id): resume})

        assert resolver.resolve(_cite(7, 0, 5, "PROFE")) is None
        assert resolver.dropped == 1

    def test_empty_span_is_dropped(self, resume: Document) -> None:
        block = DocumentBlock(
            title="Résumé", text=resume.normalized_text, document_id=str(resume.id)
        )
        resolver = CitationResolver([block], {str(resume.id): resume})

        assert resolver.resolve(_cite(0, 12, 12, "")) is None


class TestNumbering:
    def test_indices_start_at_one_and_increment(self, resume: Document) -> None:
        text = resume.normalized_text
        block = DocumentBlock(title="Résumé", text=text, document_id=str(resume.id))
        resolver = CitationResolver([block], {str(resume.id): resume})

        first = resolver.resolve(_cite(0, 0, 20, text[0:20]))
        second = resolver.resolve(_cite(0, 25, 45, text[25:45]))

        assert first is not None and second is not None
        assert (first.index, second.index) == (1, 2)

    def test_the_same_span_cited_twice_reuses_its_number(self, resume: Document) -> None:
        """Models re-cite. Two marks on one span reads as two pieces of evidence."""
        text = resume.normalized_text
        block = DocumentBlock(title="Résumé", text=text, document_id=str(resume.id))
        resolver = CitationResolver([block], {str(resume.id): resume})

        first = resolver.resolve(_cite(0, 0, 20, text[0:20]))
        again = resolver.resolve(_cite(0, 0, 20, text[0:20]))

        assert first is not None and again is not None
        assert first.index == again.index == 1

    def test_the_same_span_in_two_documents_gets_two_numbers(
        self, session: Session, resume: Document
    ) -> None:
        job = ingest_text(session=session, kind=DocumentKind.JOB, text=RESUME, label="Job").document
        blocks = [
            DocumentBlock(title="Résumé", text=resume.normalized_text, document_id=str(resume.id)),
            DocumentBlock(title="Job", text=job.normalized_text, document_id=str(job.id)),
        ]
        resolver = CitationResolver(blocks, load_documents(blocks))

        first = resolver.resolve(_cite(0, 0, 20, resume.normalized_text[0:20]))
        second = resolver.resolve(_cite(1, 0, 20, job.normalized_text[0:20]))

        assert first is not None and second is not None
        assert first.index == 1
        assert second.index == 2, "same text, different document — different evidence"


def test_load_documents_fetches_every_referenced_document_once(
    session: Session, resume: Document
) -> None:
    job = ingest_text(session=session, kind=DocumentKind.JOB, text=RESUME, label="Job").document
    blocks = [
        DocumentBlock(title="Résumé", text="", document_id=str(resume.id)),
        DocumentBlock(title="Job", text="", document_id=str(job.id), chunk_id="c1"),
        DocumentBlock(title="Job", text="", document_id=str(job.id), chunk_id="c2"),
    ]

    documents = load_documents(blocks)

    assert set(documents) == {str(resume.id), str(job.id)}
