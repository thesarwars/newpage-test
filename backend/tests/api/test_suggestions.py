"""Suggestion chips, and the extractor quality they depend on.

The chips are the first thing a user reads after loading documents, and they are
rendered verbatim, so they are also the place where a weak skill name stops being
an indexing detail and becomes a sentence addressed to a person.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.analysis import suggestions
from apps.analysis.extractors.deterministic import _primary_skill, is_curated
from apps.core.models import Session
from apps.documents.demo import seed
from apps.documents.ingest import ingest_text
from apps.documents.models import DocumentKind
from apps.observability.models import LLMCall


class TestSkillExtraction:
    @pytest.mark.parametrize(
        ("bullet", "expected"),
        [
            # A noun phrase spanning a conjunction is not a skill. It matches
            # nothing in a résumé, so it is always reported missing, and it is
            # rendered verbatim in the Gap Matrix.
            ("6+ years in backend or platform engineering.", "backend"),
            ("Logistics or supply chain domain experience.", "logistics"),
            ("Contract or consumer-driven testing.", "contract"),
            ("Experience with quantisation or model distillation.", "quantisation"),
            # A clause boundary ends the skill too.
            (
                "Experience with distributed training: data and model parallelism.",
                "distributed training",
            ),
            # Leading gerunds are filler, like the adjectives beside them.
            ("4+ years building production backend services.", "backend services"),
        ],
    )
    def test_fallback_names_are_usable(self, bullet: str, expected: str) -> None:
        assert _primary_skill(bullet) == expected

    def test_vocabulary_hits_are_unaffected(self) -> None:
        """The fallback fix must not disturb the curated path."""
        assert _primary_skill("Strong Go.") == "go"
        assert _primary_skill("Production Kubernetes experience.") == "kubernetes"
        assert _primary_skill("Strong Python, with production PyTorch experience.") == "pytorch"

    def test_curated_and_guessed_names_are_distinguishable(self) -> None:
        """The distinction the chip ordering rests on."""
        assert is_curated("kubernetes")
        assert not is_curated("backend services")


@pytest.mark.django_db
class TestSuggestions:
    def test_leads_with_a_named_gap_rather_than_a_guessed_one(self, session: Session) -> None:
        """A curated skill is a technology; a fallback one is a noun phrase.

        Ranked alphabetically the top chip read "Am I blocked by backend
        services?" — for a backend engineer, about a phrase lifted out of the
        posting's prose.
        """
        seed(session)

        chips = suggestions.build(session=session)

        assert chips[0].intent == "gap"
        skill = chips[0].label.removeprefix("Am I blocked by ").removesuffix("?")
        assert is_curated(skill), f"{skill!r} is a guessed name, not a technology"

    def test_offers_a_comparison_only_when_there_is_something_to_compare(
        self, session: Session
    ) -> None:
        seed(session)
        assert any(c.intent == "compare" for c in suggestions.build(session=session))

    def test_scoping_to_one_job_drops_the_comparison(self, session: Session) -> None:
        documents = seed(session)
        one_job = [str(d.id) for d in documents if d.kind == DocumentKind.JOB][:1]

        chips = suggestions.build(session=session, job_ids=one_job)

        assert not any(c.intent == "compare" for c in chips)

    def test_interview_mode_asks_different_questions(self, session: Session) -> None:
        seed(session)

        analysis = suggestions.build(session=session, mode="analysis")
        interview = suggestions.build(session=session, mode="interview")

        assert {c.label for c in analysis} != {c.label for c in interview}
        assert all(c.intent == "interview" for c in interview)

    def test_an_empty_workspace_offers_nothing_rather_than_a_lie(self, session: Session) -> None:
        """A chip that produces `no_context` is worse than no chip."""
        assert suggestions.build(session=session) == []

    def test_a_resume_with_no_jobs_still_offers_something(self, session: Session) -> None:
        ingest_text(
            session=session,
            kind=DocumentKind.RESUME,
            text="SUMMARY\nBackend engineer, eight years.\n\nSKILLS\nGo, Python, Kafka",
            label="Résumé",
        )

        chips = suggestions.build(session=session)

        assert len(chips) == 1
        assert chips[0].intent == "meta"

    def test_chips_are_stable_between_identical_calls(self, session: Session) -> None:
        """A suggestion that moves when nothing changed reads as a bug."""
        seed(session)

        assert [c.label for c in suggestions.build(session=session)] == [
            c.label for c in suggestions.build(session=session)
        ]

    def test_never_exceeds_the_row(self, session: Session) -> None:
        seed(session)
        assert len(suggestions.build(session=session)) <= suggestions.MAX_CHIPS


@pytest.mark.django_db
class TestSuggestionsEndpoint:
    def test_returns_chips_with_the_message_they_will_send(self, session_client: APIClient) -> None:
        session_client.post("/api/v1/sessions/demo/")

        response = session_client.get("/api/v1/suggestions/")

        assert response.status_code == 200
        chips = response.data["suggestions"]
        assert chips
        for chip in chips:
            # The label is short enough for a chip; the message is the real
            # question. Sending the label would ask a much vaguer question than
            # the one the user thinks they clicked.
            assert len(chip["label"]) < len(chip["message"])
            assert chip["intent"]

    def test_costs_nothing(self, session_client: APIClient) -> None:
        """Zero LLM calls, which is what makes this work with no API key."""
        session_client.post("/api/v1/sessions/demo/")

        session_client.get("/api/v1/suggestions/")

        assert LLMCall.objects.count() == 0

    def test_scope_narrows_the_chips(self, session_client: APIClient, session: Session) -> None:
        documents = seed(session)
        job = next(d for d in documents if d.kind == DocumentKind.JOB)

        response = session_client.get(f"/api/v1/suggestions/?scope={job.id}")

        assert not any(c["intent"] == "compare" for c in response.data["suggestions"])

    def test_requires_a_session(self, client: APIClient) -> None:
        assert client.get("/api/v1/suggestions/").status_code == 401
