"""Session lifecycle and the cookie contract."""

from __future__ import annotations

import pytest
from django.core import signing
from rest_framework.test import APIClient

from apps.core.middleware import SESSION_COOKIE_NAME, SESSION_COOKIE_SALT
from apps.core.models import Session
from tests.conftest import authenticate

pytestmark = pytest.mark.django_db


def test_create_session_sets_an_httponly_signed_cookie(client: APIClient) -> None:
    response = client.post("/api/v1/sessions/")

    assert response.status_code == 201
    cookie = response.cookies[SESSION_COOKIE_NAME]
    assert cookie["httponly"], "session cookie must not be readable by JavaScript"
    assert cookie["samesite"] == "Lax"

    # The cookie carries a signed token, never the raw one.
    session = Session.objects.get()
    assert session.token not in cookie.value
    assert signing.loads(cookie.value, salt=SESSION_COOKIE_SALT) == session.token


def test_create_session_is_idempotent(client: APIClient, session: Session) -> None:
    """A retry, or React strict mode double-invoking an effect, must not mint rows."""
    authenticate(client, session)

    response = client.post("/api/v1/sessions/")

    assert response.status_code == 200
    assert response.json()["id"] == str(session.id)
    assert Session.objects.count() == 1


def test_current_session_requires_a_session(client: APIClient) -> None:
    response = client.get("/api/v1/sessions/current/")

    assert response.status_code == 401
    assert response.json()["error_code"] == "session_required"
    assert "hint" in response.json()


def test_current_session_returns_usage(session_client: APIClient, session: Session) -> None:
    body = session_client.get("/api/v1/sessions/current/").json()

    assert body["id"] == str(session.id)
    assert body["usage"] == {
        "tokens_used": 0,
        "cost_usd": "0.000000",
        # The budget meter reads this rather than computing it client-side, so a
        # fresh session already reports the full daily ceiling as available.
        "budget_remaining_usd": "10.000000",
    }


def test_cost_is_serialized_identically_on_create_and_read(client: APIClient) -> None:
    """A freshly created session and one read back must render cost the same.

    In-memory Decimal("0") stringifies as "0"; the same value round-tripped
    through a DECIMAL(10,6) column stringifies as "0.000000". The client
    renders this as text, so the two endpoints disagreeing is a visible bug.
    """
    created = client.post("/api/v1/sessions/").json()
    fetched = client.get("/api/v1/sessions/current/").json()

    assert created["usage"]["cost_usd"] == fetched["usage"]["cost_usd"] == "0.000000"


def test_current_session_slides_the_ttl(session_client: APIClient, session: Session) -> None:
    before = session.expires_at

    session_client.get("/api/v1/sessions/current/")

    session.refresh_from_db()
    assert session.expires_at > before


def test_delete_session_removes_the_row_and_clears_the_cookie(
    session_client: APIClient, session: Session
) -> None:
    """'Delete everything' has to actually delete."""
    response = session_client.delete("/api/v1/sessions/current/")

    assert response.status_code == 204
    assert not Session.objects.filter(pk=session.pk).exists()
    assert response.cookies[SESSION_COOKIE_NAME].value == ""


def test_tampered_cookie_is_rejected_as_anonymous(client: APIClient, session: Session) -> None:
    """A forged cookie must not resolve to a workspace."""
    client.cookies[SESSION_COOKIE_NAME] = "not-a-signed-value"

    response = client.get("/api/v1/sessions/current/")

    assert response.status_code == 401


def test_expired_session_is_rejected(client: APIClient, session: Session) -> None:
    from datetime import timedelta

    from django.utils import timezone

    session.expires_at = timezone.now() - timedelta(seconds=1)
    session.save(update_fields=["expires_at"])
    authenticate(client, session)

    response = client.get("/api/v1/sessions/current/")

    assert response.status_code == 401


def test_request_id_is_echoed_for_correlation(client: APIClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "abc-123"})

    assert response["X-Request-ID"] == "abc-123"


def test_request_id_is_minted_when_absent(client: APIClient) -> None:
    assert client.get("/healthz")["X-Request-ID"]
