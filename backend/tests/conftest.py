from __future__ import annotations

import pytest
from django.core import signing
from rest_framework.test import APIClient

from apps.core.middleware import SESSION_COOKIE_NAME, SESSION_COOKIE_SALT
from apps.core.models import Session


@pytest.fixture
def session(db: None) -> Session:
    return Session.objects.create()


@pytest.fixture
def other_session(db: None) -> Session:
    """A second tenant. Exists so cross-tenant access has something to fail against."""
    return Session.objects.create()


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def authenticate(client: APIClient, session: Session) -> APIClient:
    """Attach the signed session cookie, the way a browser would."""
    client.cookies[SESSION_COOKIE_NAME] = signing.dumps(session.token, salt=SESSION_COOKIE_SALT)
    return client


@pytest.fixture
def session_client(client: APIClient, session: Session) -> APIClient:
    return authenticate(client, session)
