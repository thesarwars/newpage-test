"""Analysis endpoints.

Currently one: the suggestion chips. The Fit Board and Gap Matrix land here in
M8 and M9.
"""

from __future__ import annotations

from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.analysis import suggestions
from apps.core.errors import SessionRequiredError
from apps.core.models import Session


def _current(request: Request) -> Session:
    session: Session | None = getattr(request, "cia_session", None)
    if session is None:
        raise SessionRequiredError()
    return session


@api_view(["GET"])
def suggestion_chips(request: Request) -> Response:
    """`GET /api/v1/suggestions/?scope=<id>,<id>&mode=analysis|interview`

    Zero LLM calls. Every input is a row the deterministic extractor wrote at
    ingest, so the chips are identical with and without an API key — which is
    the state a reviewer is in when they most need a question to ask.
    """
    session = _current(request)

    scope = request.query_params.get("scope", "")
    job_ids = [part for part in scope.split(",") if part.strip()]
    mode = request.query_params.get("mode", "analysis")

    chips = suggestions.build(session=session, job_ids=job_ids or None, mode=mode)

    return Response(
        {
            "suggestions": [
                {"label": chip.label, "message": chip.message, "intent": chip.intent}
                for chip in chips
            ]
        }
    )
