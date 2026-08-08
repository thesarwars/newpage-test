"""The error envelope.

Every failure the API returns has the same shape:

    {"error_code": "too_many_pages", "message": "...", "hint": "...", "retry_after": 38}

`message` is user-facing copy rendered verbatim by the client; `hint` is the
actionable next step. Both are written here, server-side, on purpose: error UX
written in a `catch` block at 2am is how you end up shipping "Something went
wrong". If the server knows why a request failed, it also knows what the user
should do about it, and that belongs in the same place.

`error_code` is the stable, machine-readable half — the client switches on it,
tests assert on it, and it never changes for copy reasons.
"""

from __future__ import annotations

from typing import Any

import structlog
from django.core.exceptions import PermissionDenied
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

log = structlog.get_logger(__name__)


class ApiError(Exception):
    """Base for every deliberate, user-visible failure."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    error_code: str = "bad_request"
    message: str = "The request could not be completed."
    hint: str | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        hint: str | None = None,
        error_code: str | None = None,
        status_code: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        self.message = message or self.message
        self.hint = hint if hint is not None else self.hint
        self.error_code = error_code or self.error_code
        self.status_code = status_code or self.status_code
        self.retry_after = retry_after
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.hint:
            payload["hint"] = self.hint
        if self.retry_after is not None:
            payload["retry_after"] = self.retry_after
        return payload


class SessionRequiredError(ApiError):
    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "session_required"
    message = "This request needs an active workspace."
    hint = "Reload the page — a workspace is created automatically."


class NotFoundError(ApiError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"
    message = "That item does not exist in this workspace."


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """DRF exception handler that renders everything through the envelope."""
    if isinstance(exc, ApiError):
        response = Response(exc.to_payload(), status=exc.status_code)
        if exc.retry_after is not None:
            response["Retry-After"] = str(exc.retry_after)
        return response

    # A 404 raised by get_object_or_404 must not leak whether the id exists in
    # *another* session — the tenancy filter already made it invisible, and the
    # message here keeps it that way.
    if isinstance(exc, Http404):
        return Response(NotFoundError().to_payload(), status=status.HTTP_404_NOT_FOUND)

    if isinstance(exc, PermissionDenied):
        return Response(
            ApiError(
                "You do not have access to that.",
                error_code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            ).to_payload(),
            status=status.HTTP_403_FORBIDDEN,
        )

    drf_response = drf_exception_handler(exc, context)
    if drf_response is None:
        # Genuinely unexpected. Log it with the request id already bound to the
        # context, and return an opaque envelope — an exception string can
        # contain document text, and this module's whole job is that it doesn't.
        log.exception("unhandled_exception", exc_type=type(exc).__name__)
        return Response(
            {
                "error_code": "internal_error",
                "message": "Something failed on our side.",
                "hint": "Retry in a moment. If it persists the request id in the response header identifies this failure.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Normalise DRF's own validation/throttle shapes into the envelope, so a
    # client only ever has to parse one error format.
    detail = drf_response.data
    if isinstance(detail, dict) and "detail" in detail:
        message = str(detail["detail"])
    else:
        message = "The request was not valid."

    code = "validation_error" if drf_response.status_code == 400 else "request_failed"
    if drf_response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        code = "rate_limited"

    payload: dict[str, Any] = {"error_code": code, "message": message}
    if isinstance(detail, dict) and "detail" not in detail:
        payload["fields"] = detail
    if retry_after := drf_response.get("Retry-After"):
        payload["retry_after"] = int(float(retry_after))

    drf_response.data = payload
    return drf_response
