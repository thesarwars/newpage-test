"""Session endpoints.

Four operations. Two of them are the point.

`DELETE /sessions/current/` is a first-class control, not a buried setting. This
app holds a résumé; "delete everything" has to be one click and it has to
actually delete.

`POST /sessions/demo/` is the difference between a reviewer seeing this system
work and a reviewer seeing an empty screen — see apps/documents/demo.py.

`GET /sessions/current/` hydrates the whole workspace in one round trip rather
than making the client fan out to four endpoints on every page load, each of
which can fail separately and each of which needs its own loading state.
"""

from __future__ import annotations

from typing import Any

import structlog
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.errors import ApiError, SessionRequiredError
from apps.core.middleware import clear_session_cookie, set_session_cookie
from apps.core.models import Session
from apps.documents.demo import WorkspaceNotEmptyError, is_seedable, seed
from llm import backends, budget

log = structlog.get_logger(__name__)


def _serialize(session: Session, *, hydrate: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(session.id),
        "expires_at": session.expires_at.isoformat(),
        "usage": {
            "tokens_used": session.tokens_used,
            # Quantized, not str(Decimal): a freshly created session holds
            # Decimal("0") in memory and renders "0", while one read back from
            # Postgres renders "0.000000". Same field, two shapes, depending on
            # which endpoint you hit — and the client formats it as text.
            "cost_usd": f"{session.cost_usd:.6f}",
            "budget_remaining_usd": f"{budget.remaining():.6f}",
        },
        "demo_seeded": session.demo_seeded,
        "can_seed_demo": is_seedable(session),
        # Reported, never required. A missing key is a reduced-capability state,
        # not an unhealthy one — the client renders a banner, not an error.
        "demo_mode": not backends.has_live_backend(),
    }

    if hydrate:
        payload["documents"] = _documents(session)
        payload["messages"] = _messages(session)

    return payload


def _documents(session: Session) -> list[dict[str, Any]]:
    # Imported inside the function on purpose. `core` is the base every other app
    # depends on, and importing their view modules at module scope would invert
    # that. Hydration is the one place core legitimately needs to know what the
    # other apps hold, and confining the coupling to two lines keeps it obvious.
    from apps.documents.api import _serialize as serialize_document
    from apps.documents.models import Document, rail_order

    queryset = rail_order(Document.objects.for_session(session).prefetch_related("sections"))
    return [serialize_document(document) for document in queryset]


def _messages(session: Session) -> list[dict[str, Any]]:
    from apps.chat.api import _citation, _message
    from apps.chat.models import Citation, Message

    rows = list(Message.objects.for_session(session).order_by("created_at"))
    by_message: dict[Any, list[dict[str, Any]]] = {}
    for citation in Citation.objects.filter(message__in=rows).order_by("index"):
        by_message.setdefault(citation.message_id, []).append(_citation(citation))
    return [_message(message, by_message.get(message.pk, [])) for message in rows]


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
    return Response(_serialize(session, hydrate=True))


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


@api_view(["POST"])
def seed_demo(request: Request) -> Response:
    """`POST /api/v1/sessions/demo/` — load the demo corpus.

    201, not the 202 the plan specified: ingestion here is synchronous, and
    202 Accepted promises a resource that does not exist yet. By the time this
    responds the documents are parsed, chunked, embedded and queryable, so
    saying so is both true and more useful — the client renders the rail from
    this response instead of polling for it.
    """
    session = _current(request)

    try:
        documents = seed(session)
    except WorkspaceNotEmptyError:
        raise ApiError(
            "This workspace already has documents in it.",
            error_code="workspace_not_empty",
            hint="Delete everything first if you want to start from the demo corpus.",
            status_code=status.HTTP_409_CONFLICT,
        ) from None

    from apps.documents.api import _serialize as serialize_document

    return Response(
        {
            "document_ids": [str(document.id) for document in documents],
            "documents": [serialize_document(document) for document in documents],
        },
        status=status.HTTP_201_CREATED,
    )
