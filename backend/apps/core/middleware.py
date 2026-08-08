"""Request-scoped middleware: correlation ids and the anonymous session cookie."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

import structlog
from django.conf import settings
from django.core import signing
from django.http import HttpRequest, HttpResponse

from apps.core.logging import session_hash
from apps.core.models import Session

log = structlog.get_logger(__name__)

SESSION_COOKIE_NAME = "cis_session"
SESSION_COOKIE_SALT = "cia.session.v1"
REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware:
    """Bind a correlation id to the log context for the life of the request.

    Honours an inbound `X-Request-ID` so a trace survives a proxy, and echoes it
    back so a reviewer reading a failed response can grep for exactly that
    request. Everything logged downstream inherits it via contextvars — no
    threading a logger through call signatures.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.request_id = request_id  # type: ignore[attr-defined]

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.path,
        )

        started = time.monotonic()
        response = self.get_response(request)
        duration_ms = int((time.monotonic() - started) * 1000)

        response[REQUEST_ID_HEADER] = request_id

        # One structured line per request. Not the URL query string: a search
        # query is user content and this app's logs do not carry user content.
        log.info(
            "request",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        structlog.contextvars.clear_contextvars()
        return response


class SessionCookieMiddleware:
    """Resolve the anonymous workspace from a signed httpOnly cookie.

    Resolution only — this never *creates* a session. Creation is an explicit
    `POST /api/v1/sessions/`, so a crawler hitting the site does not mint rows,
    and so the client always knows when it got a new workspace.

    Sets `request.cia_session` to a `Session` or `None`. Nothing else in the
    codebase reads the cookie.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.cia_session = self._resolve(request)  # type: ignore[attr-defined]

        if request.cia_session is not None:  # type: ignore[attr-defined]
            structlog.contextvars.bind_contextvars(
                session_hash=session_hash(
                    request.cia_session.token,  # type: ignore[attr-defined]
                    secret=settings.SECRET_KEY,
                )
            )

        return self.get_response(request)

    @staticmethod
    def _resolve(request: HttpRequest) -> Session | None:
        raw = request.COOKIES.get(SESSION_COOKIE_NAME)
        if not raw:
            return None
        try:
            token = signing.loads(raw, salt=SESSION_COOKIE_SALT)
        except signing.BadSignature:
            # A tampered or stale-secret cookie is treated as no cookie. Logged
            # without the value: it is attacker-controlled input.
            log.info("session_cookie_rejected", reason="bad_signature")
            return None

        session = Session.objects.filter(token=token).first()
        if session is None:
            log.info("session_cookie_rejected", reason="unknown_token")
            return None
        if session.is_expired:
            log.info("session_cookie_rejected", reason="expired")
            return None
        return session


def set_session_cookie(response: HttpResponse, session: Session) -> None:
    """Attach the signed session cookie.

    httpOnly so client JavaScript cannot read it (an XSS then cannot exfiltrate
    the workspace); SameSite=Lax because the only cross-origin caller is our own
    frontend on localhost; `secure` follows the environment so local HTTP works
    and a deployed environment does not silently downgrade.
    """
    response.set_cookie(
        SESSION_COOKIE_NAME,
        signing.dumps(session.token, salt=SESSION_COOKIE_SALT),
        max_age=int((session.expires_at - session.created_at).total_seconds()),
        httponly=True,
        samesite="Lax",
        secure=not settings.DEBUG,
        path="/",
    )


def clear_session_cookie(response: HttpResponse) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
