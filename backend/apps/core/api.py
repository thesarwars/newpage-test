"""Session endpoints.

Three operations, and the third is the point: `DELETE /sessions/current/` is a
first-class control, not a buried setting. This app holds a résumé; "delete
everything" has to be one click and it has to actually delete.
"""

from __future__ import annotations

from typing import Any

import structlog
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.errors import SessionRequiredError
from apps.core.middleware import clear_session_cookie, set_session_cookie
from apps.core.models import Session

log = structlog.get_logger(__name__)


def _serialize(session: Session) -> dict[str, Any]:
    return {
        "id": str(session.id),
        "expires_at": session.expires_at.isoformat(),
        "usage": {
            "tokens_used": session.tokens_used,
            # Quantized, not str(Decimal): a freshly created session holds
            # Decimal("0") in memory and renders "0", while one read back from
            # Postgres renders "0.000000". Same field, two shapes, depending on
            # which endpoint you hit — and the client formats it as text.
            "cost_usd": f"{session.cost_usd:.6f}",
        },
        "demo_seeded": session.demo_seeded,
        # Documents and messages join this payload as those models land (M2/M5).
    }


def _current(request: Request) -> Session:
    session: Session | None = getattr(request, "cia_session", None)
    if session is None:
        raise SessionRequiredError()
    return session


@api_view(["POST"])
def create_session(request: Request) -> Response:
    """Create a workspace, or return the existing one.

    Idempotent on purpose: a client that retries — or a React strict-mode double
    effect in development — must not leave orphaned sessions behind.
    """
    existing: Session | None = getattr(request, "cia_session", None)
    if existing is not None:
        response = Response(_serialize(existing), status=status.HTTP_200_OK)
        set_session_cookie(response, existing)
        return response

    session = Session.objects.create()
    log.info("session_created", session_id=str(session.id))

    response = Response(_serialize(session), status=status.HTTP_201_CREATED)
    set_session_cookie(response, session)
    return response


@api_view(["GET", "DELETE"])
def current_session(request: Request) -> Response:
    """GET hydrates the workspace; DELETE destroys it.

    One path, two methods — deletion is not a separate resource, it is the
    absence of this one.
    """
    session = _current(request)

    if request.method == "DELETE":
        return _delete(session)

    session.touch()
    return Response(_serialize(session))


def _delete(session: Session) -> Response:
    """Hard delete: rows now, and the cookie with them.

    Cascades to every session-scoped model. Uploaded blobs are unlinked by the
    documents app's delete signal once that model exists (M2).
    """
    session_id = str(session.id)
    session.delete()
    log.info("session_deleted", session_id=session_id)

    response = Response(status=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response
