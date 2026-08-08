"""CORS, tested here because a browser is the only other place it fails.

The frontend runs on :3000 and the API on :8000, and every request carries the
session cookie. That combination has exactly one failure mode worth guarding:
`Access-Control-Allow-Credentials` missing or the origin echoed as `*`, either of
which makes the browser silently drop the cookie. Nothing in the Django test
client exercises it, and nothing in the backend suite notices — the endpoints
keep returning 200 to a client that no longer has a session.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from rest_framework.test import APIClient

WEB_ORIGIN = "http://localhost:3000"


@pytest.mark.django_db
class TestPreflight:
    def test_credentialed_preflight_is_allowed_from_the_web_origin(self, client: APIClient) -> None:
        response = client.options(
            "/api/v1/sessions/demo/",
            HTTP_ORIGIN=WEB_ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type",
        )

        assert response.status_code == 200
        assert response["access-control-allow-origin"] == WEB_ORIGIN
        # Without this the browser drops the Set-Cookie and every subsequent
        # request arrives anonymous — with no error the client can see.
        assert response["access-control-allow-credentials"] == "true"

    def test_the_origin_is_echoed_not_wildcarded(self, client: APIClient) -> None:
        """`*` and credentials are mutually exclusive in the CORS spec, so a
        wildcard here would be the same failure wearing a permissive face."""
        response = client.options(
            "/api/v1/sessions/",
            HTTP_ORIGIN=WEB_ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )

        assert response["access-control-allow-origin"] != "*"

    def test_an_unlisted_origin_gets_no_cors_headers(self, client: APIClient) -> None:
        response = client.options(
            "/api/v1/sessions/",
            HTTP_ORIGIN="https://evil.example.com",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )

        assert "access-control-allow-origin" not in response


def test_the_web_origin_is_actually_in_the_allowlist() -> None:
    """Guards the default, not the middleware: a .env that forgets
    CORS_ALLOWED_ORIGINS produces an app whose frontend cannot talk to it."""
    assert WEB_ORIGIN in settings.CORS_ALLOWED_ORIGINS
    assert settings.CORS_ALLOW_CREDENTIALS is True
