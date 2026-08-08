"""Context assembly: what goes in the request, in what order, and what doesn't."""

from __future__ import annotations

import pytest

from apps.chat import context
from apps.chat.models import Message, Mode, Role
from apps.core.models import Session
from apps.documents.ingest import ingest_text
from apps.documents.models import Chunk, Document, DocumentKind

RESUME = """PROFESSIONAL SUMMARY
Backend engineer with eight years building payment systems.

EXPERIENCE
Senior Backend Engineer — Meridian Logistics (2022 to Present)
Reduced p99 latency from 1.4s to 380ms across the dispatch API.

SKILLS
Go, Python, PostgreSQL, Kafka, Terraform, AWS
"""

JOB = """Senior Platform Engineer
Helios Freight

REQUIREMENTS
- 5+ years of backend engineering in Go or Python
- Production experience operating Kubernetes at scale
"""


@pytest.fixture
def corpus(session: Session) -> tuple[Document, Document]:
    resume = ingest_text(
        session=session, kind=DocumentKind.RESUME, text=RESUME, label="Résumé"
    ).document
    job = ingest_text(session=session, kind=DocumentKind.JOB, text=JOB, label="Helios").document
    return resume, job


@pytest.mark.django_db
class TestBlocks:
    def test_resume_goes_in_whole_with_a_zero_offset(
        self, session: Session, corpus: tuple[Document, Document]
    ) -> None:
        """No threshold, no second path, no offset arithmetic. That is the point."""
        resume, _ = corpus

        request = context.build(session=session, question="anything", chunks=[])

        assert len(request.blocks) == 1
        block = request.blocks[0]
        assert block.text == resume.normalized_text
        assert block.base_offset == 0
        assert block.cache is True

    def test_job_chunks_carry_their_char_start_as_the_base_offset(
        self, session: Session, corpus: tuple[Document, Document]
    ) -> None:
        _, job = corpus
        chunks = list(Chunk.objects.filter(document=job).order_by("ordinal"))

        request = context.build(session=session, question="anything", chunks=chunks)

        for block in request.blocks[1:]:
            chunk = Chunk.objects.get(pk=block.chunk_id)
            assert block.base_offset == chunk.char_start
            # The invariant this arithmetic depends on, asserted where it is used.
            assert job.normalized_text[chunk.char_start : chunk.char_end] == block.text

    def test_chunk_blocks_carry_the_breadcrumb_as_context_not_as_text(
        self, session: Session, corpus: tuple[Document, Document]
    ) -> None:
        """`context` is not citable text, so it cannot shift a single offset."""
        _, job = corpus
        chunk = Chunk.objects.filter(document=job).order_by("ordinal").first()
        assert chunk is not None

        request = context.build(session=session, question="anything", chunks=[chunk])
        block = request.blocks[-1]

        assert block.context
        assert block.context not in block.text
        assert block.text == chunk.text

    def test_only_the_resume_block_is_cached(
        self, session: Session, corpus: tuple[Document, Document]
    ) -> None:
        """Retrieved chunks change every turn; caching them would churn the prefix."""
        _, job = corpus
        chunks = list(Chunk.objects.filter(document=job))

        request = context.build(session=session, question="anything", chunks=chunks)

        assert [b.cache for b in request.blocks] == [True] + [False] * len(chunks)

    def test_no_resume_still_produces_a_valid_request(self, session: Session) -> None:
        request = context.build(session=session, question="anything", chunks=[])

        assert request.blocks == []
        assert request.question == "anything"


@pytest.mark.django_db
class TestPrompts:
    def test_mode_selects_the_suffix_and_leaves_the_body_alone(self, session: Session) -> None:
        analysis = context.build(session=session, question="q", chunks=[], mode=Mode.ANALYSIS)
        interview = context.build(session=session, question="q", chunks=[], mode=Mode.INTERVIEW)

        assert analysis.system == interview.system, "the cached prefix must not vary by mode"
        assert analysis.system_suffix != interview.system_suffix


@pytest.mark.django_db
class TestHistory:
    def _turn(self, session: Session, role: str, content: str) -> Message:
        return Message.objects.create(session=session, role=role, content=content)

    def test_history_alternates_and_starts_with_a_user_turn(self, session: Session) -> None:
        self._turn(session, Role.USER, "first question")
        self._turn(session, Role.ASSISTANT, "first answer")

        request = context.build(session=session, question="next", chunks=[])

        assert [role for role, _ in request.history] == ["user", "assistant"]

    def test_a_dangling_assistant_turn_at_the_front_is_dropped(self, session: Session) -> None:
        """Budget trimming can behead the conversation. The API 400s on that."""
        self._turn(session, Role.ASSISTANT, "orphaned answer")
        self._turn(session, Role.USER, "question")
        self._turn(session, Role.ASSISTANT, "answer")

        request = context.build(session=session, question="next", chunks=[])

        assert request.history[0][0] == "user"

    def test_consecutive_same_role_turns_are_merged(self, session: Session) -> None:
        self._turn(session, Role.USER, "one")
        self._turn(session, Role.USER, "two")
        self._turn(session, Role.ASSISTANT, "answer")

        request = context.build(session=session, question="next", chunks=[])

        roles = [role for role, _ in request.history]
        assert roles == ["user", "assistant"]
        assert "one" in request.history[0][1] and "two" in request.history[0][1]

    def test_a_trailing_user_turn_is_dropped(self, session: Session) -> None:
        """It would collide with the question turn the gateway appends."""
        self._turn(session, Role.USER, "question")
        self._turn(session, Role.ASSISTANT, "answer")
        self._turn(session, Role.USER, "unanswered question")

        request = context.build(session=session, question="next", chunks=[])

        assert request.history[-1][0] == "assistant"

    def test_only_the_last_six_turns_are_carried(self, session: Session) -> None:
        for i in range(20):
            role = Role.USER if i % 2 == 0 else Role.ASSISTANT
            self._turn(session, role, f"turn {i}")

        request = context.build(session=session, question="next", chunks=[])

        assert len(request.history) <= context.MAX_HISTORY_TURNS

    def test_a_turn_over_budget_is_dropped_whole(self, session: Session) -> None:
        """Half a message is worse than none — a severed answer reads as truncated."""
        self._turn(session, Role.USER, "short question")
        self._turn(session, Role.ASSISTANT, "word " * 6000)

        request = context.build(session=session, question="next", chunks=[])

        for _, text in request.history:
            assert not text.startswith("word word") or len(text) < 30_000

    def test_history_is_scoped_to_the_session(
        self, session: Session, other_session: Session
    ) -> None:
        self._turn(other_session, Role.USER, "somebody else's question")
        self._turn(other_session, Role.ASSISTANT, "somebody else's answer")

        request = context.build(session=session, question="next", chunks=[])

        assert request.history == []
