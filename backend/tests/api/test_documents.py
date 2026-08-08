"""Document endpoints, end to end against a real database."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.core.models import Session
from apps.documents.models import Document, DocumentKind, DocumentStatus
from tests.conftest import authenticate


class ClientResponse(Protocol):
    """What the test client returns, as far as these tests care.

    Structural rather than nominal on purpose. django-stubs names the concrete
    type with a private, version-dependent symbol (`_MonkeyPatchedWSGIResponse`),
    and `rest_framework.Response` — the obvious guess — is what the *view*
    returns and has no `.json()`. Declaring the two members actually used here is
    stable against both.
    """

    status_code: int

    def json(self) -> Any: ...


pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

JOB_TEXT = """\
Staff Backend Engineer

REQUIREMENTS
- Production Kubernetes experience.
- Terraform in production.
- Strong Go.

BENEFITS
- Fully remote within the UK.
- Learning budget of GBP 2,000 a year.
"""


def upload(client: APIClient, name: str, kind: str = "job") -> ClientResponse:
    payload = (FIXTURES / name).read_bytes()
    return client.post(
        "/api/v1/documents/",
        {
            "file": SimpleUploadedFile(Path(name).name, payload, content_type="application/pdf"),
            "kind": kind,
        },
        format="multipart",
    )


def paste(
    client: APIClient, text: str = JOB_TEXT, kind: str = "job", label: str = ""
) -> ClientResponse:
    return client.post(
        "/api/v1/documents/paste/",
        {"kind": kind, "text": text, "label": label},
        format="json",
    )


class TestUpload:
    def test_pdf_upload_produces_a_ready_document_with_sections(
        self, session_client: APIClient
    ) -> None:
        response = upload(session_client, "demo/job_2_vertex.pdf")

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == DocumentStatus.READY
        assert body["ordinal"] == 1
        assert body["page_count"] >= 1
        assert any(s["kind"] == "requirements" for s in body["sections"])

    def test_normalized_text_is_stored_and_retrievable(self, session_client: APIClient) -> None:
        """The evidence panel's data source, and the citation coordinate space."""
        document_id = upload(session_client, "demo/job_2_vertex.pdf").json()["id"]

        body = session_client.get(f"/api/v1/documents/{document_id}/").json()

        assert "Kubernetes" in body["normalized_text"]

    def test_section_offsets_slice_correctly_out_of_normalized_text(
        self, session_client: APIClient
    ) -> None:
        """The offset contract, asserted through the API rather than in-process.

        This is the property the whole citation feature rests on: a stored
        char_start/char_end must index into the text the client actually
        received, not into some intermediate the server threw away.
        """
        document_id = upload(session_client, "demo/job_2_vertex.pdf").json()["id"]
        body = session_client.get(f"/api/v1/documents/{document_id}/").json()
        text = body["normalized_text"]

        requirements = next(s for s in body["sections"] if s["kind"] == "requirements")
        sliced = text[requirements["char_start"] : requirements["char_end"]]

        assert "Kubernetes" in sliced
        assert "Fully remote" not in sliced  # that belongs to BENEFITS

    def test_benefits_and_legal_are_flagged_as_boilerplate(self, session_client: APIClient) -> None:
        body = upload(session_client, "demo/job_1_northwind.pdf").json()

        boilerplate = {s["kind"] for s in body["sections"] if s["is_boilerplate"]}
        assert boilerplate == {"benefits", "legal"}

    def test_adversarial_pdf_is_flagged_with_reasons(self, session_client: APIClient) -> None:
        """The quarantine badge is a product feature, so it needs data to render."""
        body = upload(session_client, "adversarial_job.pdf").json()

        assert body["injection_flag"] is True
        assert "hidden_text" in body["injection_reasons"]

    def test_clean_documents_are_not_flagged(self, session_client: APIClient) -> None:
        assert (
            upload(session_client, "demo/resume.pdf", kind="resume").json()["injection_flag"]
            is False
        )

    def test_upload_without_a_file_is_a_named_error(self, session_client: APIClient) -> None:
        response = session_client.post("/api/v1/documents/", {"kind": "job"}, format="multipart")

        assert response.status_code == 400
        assert response.json()["error_code"] == "no_file"

    def test_invalid_kind_is_rejected(self, session_client: APIClient) -> None:
        response = session_client.post(
            "/api/v1/documents/",
            {"file": SimpleUploadedFile("a.txt", b"x" * 300), "kind": "cover_letter"},
            format="multipart",
        )

        assert response.status_code == 400
        assert response.json()["error_code"] == "invalid_kind"

    def test_a_file_with_no_text_layer_points_at_the_paste_fallback(
        self, session_client: APIClient
    ) -> None:
        """The 422 must name a fallback the user can actually reach."""
        response = session_client.post(
            "/api/v1/documents/",
            {"file": SimpleUploadedFile("scan.txt", b"short"), "kind": "job"},
            format="multipart",
        )

        body = response.json()
        assert response.status_code == 422
        assert body["error_code"] == "no_text_layer"
        assert "paste" in body["hint"].lower()


class TestPaste:
    def test_pasted_text_ingests_like_an_upload(self, session_client: APIClient) -> None:
        response = paste(session_client)

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == DocumentStatus.READY
        assert any(s["kind"] == "requirements" for s in body["sections"])

    def test_pasted_label_is_used(self, session_client: APIClient) -> None:
        assert paste(session_client, label="Vertex Systems").json()["label"] == "Vertex Systems"

    def test_a_short_but_real_posting_is_accepted(self, session_client: APIClient) -> None:
        """Pasted text gets its own threshold, not the file one.

        A terse job blurb is ~150 characters — under the 200 used to detect a
        PDF with no text layer. Applying that here told the person who typed it
        that their document "looks like a scan", and pointed them at the paste
        box they were already using.
        """
        terse = (
            "Backend Engineer, remote. We need someone strong in Python and "
            "PostgreSQL who has run services in production. Kafka a plus."
        )
        assert 50 < len(terse) < 200

        assert paste(session_client, text=terse).status_code == 201

    def test_genuinely_empty_paste_is_rejected_with_its_own_error(
        self, session_client: APIClient
    ) -> None:
        response = paste(session_client, text="Backend Engineer")

        assert response.status_code == 422
        body = response.json()
        assert body["error_code"] == "text_too_short"
        assert "scan" not in body["message"].lower()


class TestQuotas:
    def test_a_second_resume_is_rejected(self, session_client: APIClient) -> None:
        paste(session_client, kind="resume", text=JOB_TEXT)

        response = paste(session_client, kind="resume", text=JOB_TEXT)

        assert response.status_code == 422
        assert response.json()["error_code"] == "resume_exists"

    def test_the_eleventh_job_is_rejected(self, session_client: APIClient) -> None:
        for _ in range(10):
            assert paste(session_client).status_code == 201

        response = paste(session_client)

        assert response.status_code == 422
        assert response.json()["error_code"] == "too_many_jobs"


class TestOrdinals:
    def test_jobs_number_from_one(self, session_client: APIClient) -> None:
        """'Job #0' reads as a bug to a user, and '#N' is also a retrieval filter."""
        ordinals = [paste(session_client).json()["ordinal"] for _ in range(3)]

        assert ordinals == [1, 2, 3]

    def test_resume_is_always_ordinal_zero(self, session_client: APIClient) -> None:
        assert paste(session_client, kind="resume").json()["ordinal"] == 0

    def test_deleting_a_job_renumbers_the_rest(self, session_client: APIClient) -> None:
        """Leaving a hole would mislabel every later job and collide on re-upload."""
        ids = [paste(session_client).json()["id"] for _ in range(3)]

        assert session_client.delete(f"/api/v1/documents/{ids[0]}/").status_code == 204

        remaining = session_client.get("/api/v1/documents/").json()["documents"]
        assert [d["ordinal"] for d in remaining] == [1, 2]

    def test_a_new_job_after_deletion_does_not_collide(self, session_client: APIClient) -> None:
        ids = [paste(session_client).json()["id"] for _ in range(2)]
        session_client.delete(f"/api/v1/documents/{ids[0]}/")

        assert paste(session_client).status_code == 201


class TestTenancy:
    """The cross-tenant probe. docs/PLAN.md marks this never-cut."""

    def test_another_sessions_document_is_indistinguishable_from_a_missing_one(
        self, client: APIClient, session: Session, other_session: Session
    ) -> None:
        authenticate(client, other_session)
        foreign_id = paste(client).json()["id"]

        authenticate(client, session)
        response = client.get(f"/api/v1/documents/{foreign_id}/")

        assert response.status_code == 404
        # Same body as a genuinely absent id: a distinguishable 403 would confirm
        # the document exists, which is itself a leak.
        assert response.json()["error_code"] == "not_found"

    def test_listing_never_crosses_sessions(
        self, client: APIClient, session: Session, other_session: Session
    ) -> None:
        authenticate(client, other_session)
        paste(client)

        authenticate(client, session)
        assert client.get("/api/v1/documents/").json()["documents"] == []

    def test_deleting_another_sessions_document_is_a_404(
        self, client: APIClient, session: Session, other_session: Session
    ) -> None:
        authenticate(client, other_session)
        foreign_id = paste(client).json()["id"]

        authenticate(client, session)
        assert client.delete(f"/api/v1/documents/{foreign_id}/").status_code == 404
        assert Document.objects.filter(pk=foreign_id).exists()

    def test_documents_require_a_session(self, client: APIClient) -> None:
        assert client.get("/api/v1/documents/").status_code == 401


class TestDeletion:
    def test_deleting_a_session_cascades_to_its_documents(
        self, session_client: APIClient, session: Session
    ) -> None:
        """'Delete everything' has to reach the documents too."""
        paste(session_client)
        assert Document.objects.count() == 1

        session_client.delete("/api/v1/sessions/current/")

        assert Document.objects.count() == 0

    def test_patch_updates_the_label(self, session_client: APIClient) -> None:
        document_id = paste(session_client).json()["id"]

        response = session_client.patch(
            f"/api/v1/documents/{document_id}/",
            {"label": "Vertex Systems", "company": "Vertex"},
            format="json",
        )

        assert response.status_code == 200
        assert response.json()["label"] == "Vertex Systems"


def test_display_label_falls_back_to_job_number(session_client: APIClient) -> None:
    body = paste(session_client).json()

    assert body["label"] == "Job #1"


def test_resume_display_label(session_client: APIClient) -> None:
    assert paste(session_client, kind=DocumentKind.RESUME).json()["label"] == "Résumé"
