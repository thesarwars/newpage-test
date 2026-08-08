"""Demo seeding and workspace hydration.

The demo endpoint is the one a reviewer hits first, so its failure modes matter
more than its happy path: seeding twice, seeding on top of real uploads, and
seeding into somebody else's workspace.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.core.models import Session
from apps.documents.demo import CORPUS, FIXTURES, seed
from apps.documents.ingest import ingest_text
from apps.documents.models import Chunk, Document, DocumentKind
from tests.conftest import drain


def test_every_fixture_in_the_corpus_exists() -> None:
    """A missing fixture turns the flagship endpoint into a 500 on first click."""
    for _, filename, _ in CORPUS:
        assert (FIXTURES / filename).is_file(), f"{filename} is missing from {FIXTURES}"


@pytest.mark.django_db
class TestSeeding:
    def test_seeds_one_resume_and_three_jobs_through_the_real_pipeline(
        self, session: Session
    ) -> None:
        """Nothing is precomputed — the demo runs the same ingest an upload does,
        so what it demonstrates is the actual system."""
        documents = seed(session)

        kinds = [d.kind for d in documents]
        assert kinds.count(DocumentKind.RESUME) == 1
        assert kinds.count(DocumentKind.JOB) == 3
        for document in documents:
            assert document.normalized_text
            assert document.sections.exists()

        embedded = Chunk.objects.for_session(session).exclude(embedding=None).count()
        assert embedded == Chunk.objects.for_session(session).count() > 0

    def test_jobs_are_numbered_from_one(self, session: Session) -> None:
        """ "Job #2" has to mean something — the resolver routes on that ordinal."""
        seed(session)

        ordinals = sorted(
            Document.objects.for_session(session)
            .filter(kind=DocumentKind.JOB)
            .values_list("ordinal", flat=True)
        )
        assert ordinals == [1, 2, 3]

    def test_seeding_twice_does_not_duplicate(self, session: Session) -> None:
        """A double-click, a retry, or React's dev-mode double effect."""
        first = seed(session)
        second = seed(session)

        assert [d.id for d in first] == [d.id for d in second]
        assert Document.objects.for_session(session).count() == 4

    def test_the_order_is_the_same_on_a_repeat_seed(self, session: Session) -> None:
        """Résumé first, then jobs. The model's default ordering is (kind,
        ordinal), which sorts "job" before "resume" alphabetically — so a fresh
        seed and a reloaded one disagreed, which reads as a rendering bug."""
        first = seed(session)
        second = seed(session)

        assert [d.kind for d in first] == ["resume", "job", "job", "job"]
        assert [d.kind for d in second] == ["resume", "job", "job", "job"]

    def test_refuses_to_seed_on_top_of_real_uploads(self, session: Session) -> None:
        """Friendlier than merging: the quota would otherwise fail deep inside
        ingest with a message about résumé counts that explains nothing."""
        from apps.documents.demo import WorkspaceNotEmptyError

        ingest_text(
            session=session, kind=DocumentKind.RESUME, text="MY OWN RÉSUMÉ\n" * 20, label="Mine"
        )

        with pytest.raises(WorkspaceNotEmptyError):
            seed(session)

    def test_seeding_is_scoped_to_one_session(
        self, session: Session, other_session: Session
    ) -> None:
        seed(session)

        assert Document.objects.for_session(other_session).count() == 0


@pytest.mark.django_db
class TestDemoEndpoint:
    def test_returns_the_documents_it_created(self, session_client: APIClient) -> None:
        """Rendered directly into the rail. Polling for a synchronous result is
        latency the user watches for no reason."""
        response = session_client.post("/api/v1/sessions/demo/")

        assert response.status_code == 201
        assert len(response.data["document_ids"]) == 4
        assert len(response.data["documents"]) == 4
        assert response.data["documents"][0]["status"] == "ready"

    def test_conflicts_with_a_named_error_when_the_workspace_is_used(
        self, session_client: APIClient, session: Session
    ) -> None:
        ingest_text(
            session=session, kind=DocumentKind.RESUME, text="MY OWN RÉSUMÉ\n" * 20, label="Mine"
        )

        response = session_client.post("/api/v1/sessions/demo/")

        assert response.status_code == 409
        assert response.data["error_code"] == "workspace_not_empty"
        assert response.data["hint"]

    def test_requires_a_session(self, client: APIClient) -> None:
        response = client.post("/api/v1/sessions/demo/")

        assert response.status_code == 401
        assert response.data["error_code"] == "session_required"


@pytest.mark.django_db
class TestHydration:
    def test_current_session_returns_the_whole_workspace_in_one_call(
        self, session_client: APIClient
    ) -> None:
        """One round trip on page load, not four — each of which would need its
        own loading state and its own failure path."""
        session_client.post("/api/v1/sessions/demo/")

        response = session_client.get("/api/v1/sessions/current/")

        assert response.status_code == 200
        assert len(response.data["documents"]) == 4
        assert response.data["messages"] == []
        assert response.data["demo_seeded"] is True
        assert response.data["can_seed_demo"] is False
        assert response.data["usage"]["budget_remaining_usd"]

    def test_reports_demo_mode_without_requiring_a_key(self, session_client: APIClient) -> None:
        """The banner's data source. Reduced capability, not an error."""
        response = session_client.get("/api/v1/sessions/current/")

        assert response.data["demo_mode"] is True
        assert response.data["can_seed_demo"] is True

    def test_hydration_carries_messages_and_their_citations(
        self, session_client: APIClient
    ) -> None:
        session_client.post("/api/v1/sessions/demo/")
        # Draining is not incidental — see tests/conftest.py::drain.
        drain(
            session_client.post(
                "/api/v1/chat/", {"message": "What am I missing for Job #2?"}, format="json"
            )
        )

        response = session_client.get("/api/v1/sessions/current/")

        messages = response.data["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[1]["citations"], "a reload must be able to re-splice the marks"

    def test_creation_does_not_hydrate(self, client: APIClient) -> None:
        """POST /sessions/ runs before anything exists; a documents key there
        would be an empty list the client learns to ignore."""
        response = client.post("/api/v1/sessions/")

        assert response.status_code == 201
        assert "documents" not in response.data

    def test_hydration_is_scoped_to_the_session(
        self, client: APIClient, session_client: APIClient, other_session: Session
    ) -> None:
        from tests.conftest import authenticate

        session_client.post("/api/v1/sessions/demo/")

        intruder = authenticate(client, other_session)
        response = intruder.get("/api/v1/sessions/current/")

        assert response.data["documents"] == []
