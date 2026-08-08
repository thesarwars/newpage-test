"""Failures the frontend would otherwise meet as a bare 500.

Every case here was found by probing the running API rather than by reading the
code, and every one of them returned `internal_error` with no `error_code` a
client could switch on. A 500 is the one response this project's error envelope
is designed to make impossible, so each is either fixed or given a name.
"""

from __future__ import annotations

import threading

import pytest
from django.db import connections
from rest_framework.test import APIClient

from apps.core.models import Session
from apps.documents.ingest import ingest_text
from apps.documents.models import Document, DocumentKind
from apps.documents.validators import MAX_PASTED_CHARS
from tests.conftest import authenticate

JOB = """Senior Platform Engineer
Helios Freight

REQUIREMENTS
- 5+ years of backend engineering in Go or Python
- Production experience operating Kubernetes at scale
- Strong PostgreSQL skills, including query tuning
"""


@pytest.mark.django_db(transaction=True)
def test_concurrent_uploads_do_not_collide_on_the_ordinal() -> None:
    """Two uploads at once used to produce one 201 and one 500.

    `_enforce_session_quota` counts and `next_ordinal` takes a MAX — both
    read-then-write, both racing. The loser violated
    `unique(session, kind, ordinal)` and surfaced as an IntegrityError, which
    the user saw as "Something failed on our side."

    transaction=True because the fix is a `select_for_update`, and row locks do
    nothing inside pytest-django's shared outer transaction.
    """
    session = Session.objects.create()
    errors: list[BaseException] = []

    def upload(index: int) -> None:
        try:
            ingest_text(
                session=session,
                kind=DocumentKind.JOB,
                text=f"{JOB}\nPosting {index}.",
                label=f"Job {index}",
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            # Each thread gets its own connection; leaving them open exhausts
            # the pool and makes the *next* test fail instead of this one.
            connections.close_all()

    threads = [threading.Thread(target=upload, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == [], f"concurrent uploads raised: {errors}"
    ordinals = sorted(
        Document.objects.for_session(session)
        .filter(kind=DocumentKind.JOB)
        .values_list("ordinal", flat=True)
    )
    assert ordinals == [1, 2, 3, 4, 5, 6], "every upload must get its own ordinal"

    Session.objects.filter(pk=session.pk).delete()


@pytest.mark.django_db
class TestPasteBounds:
    def test_oversized_paste_is_refused_with_a_name(self, session_client: APIClient) -> None:
        """Ingest is synchronous, so an unbounded paste is unbounded request
        time — 500 KB measured at 51 seconds, which is a 502 behind gunicorn's
        default 30-second worker timeout rather than a slow success."""
        response = session_client.post(
            "/api/v1/documents/paste/",
            {"kind": "job", "text": "word " * (MAX_PASTED_CHARS // 2)},
            format="json",
        )

        assert response.status_code == 422
        assert response.data["error_code"] == "text_too_long"
        assert response.data["hint"]

    def test_a_paste_just_under_the_cap_is_accepted(self, session_client: APIClient) -> None:
        """The control. Without it, the test above passes if paste is broken."""
        response = session_client.post(
            "/api/v1/documents/paste/",
            {"kind": "job", "text": JOB + "x" * (MAX_PASTED_CHARS - len(JOB) - 10)},
            format="json",
        )

        assert response.status_code == 201

    def test_a_body_past_djangos_limit_is_a_413_not_a_500(self, session_client: APIClient) -> None:
        """Django rejects an oversized body while *reading* it, before any view
        runs, so no endpoint can catch it locally. It used to render as a bare
        500 with no error_code to switch on."""
        response = session_client.post(
            "/api/v1/documents/paste/",
            {"kind": "job", "text": "x" * (7 * 1024 * 1024)},
            format="json",
        )

        assert response.status_code == 413
        assert response.data["error_code"] == "payload_too_large"


@pytest.mark.django_db
def test_malformed_json_does_not_leak_a_python_diagnostic(
    session_client: APIClient,
) -> None:
    """It returned "JSON parse error - Expecting property name enclosed in
    double quotes: line 1 column 2 (char 1)". True, useless, and a client bug
    rather than a user one."""
    response = session_client.post(
        "/api/v1/documents/paste/", data="{bad", content_type="application/json"
    )

    assert response.status_code == 400
    assert response.data["error_code"] == "malformed_request"
    assert "JSON parse error" not in response.data["message"]
    assert "line 1 column" not in response.data["message"]


@pytest.mark.django_db
def test_chunk_count_is_reported(session_client: APIClient, session: Session) -> None:
    """docs/PLAN.md §6 promises it and the rail renders it. It was missing, so
    the rail had no way to show that indexing happened rather than merely
    reporting that it had."""
    ingest_text(session=session, kind=DocumentKind.JOB, text=JOB, label="Helios")

    response = session_client.get("/api/v1/documents/")

    assert response.data["documents"][0]["chunk_count"] > 0


@pytest.mark.django_db
def test_deleting_everything_leaves_a_usable_workspace(client: APIClient, session: Session) -> None:
    """DELETE clears the cookie, so the very next request is 401. The client has
    to mint a replacement immediately or a successful deletion looks like an
    expired session."""
    authed = authenticate(client, session)
    assert authed.delete("/api/v1/sessions/current/").status_code == 204

    assert client.get("/api/v1/sessions/current/").status_code == 401
    assert client.post("/api/v1/sessions/").status_code == 201
