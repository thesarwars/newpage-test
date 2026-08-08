"""Liveness, readiness and version.

The split matters operationally: `/healthz` answers "is this process alive"
and touches nothing, because a liveness probe that checks the database turns one
slow query into a cluster-wide restart storm. `/readyz` answers "can this process
serve traffic" and checks dependencies.

`/readyz` reports the Anthropic key rather than requiring it. The app is designed
to work without one (docs/PLAN.md §14) and a readiness probe that fails on a
missing optional credential would make the keyless path un-deployable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import connection
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    required: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok, "required": self.required}
        if self.detail:
            payload["detail"] = self.detail
        return payload


def _check_database() -> Check:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return Check("database", False, required=True, detail=type(exc).__name__)
    return Check("database", True, required=True)


def _check_pgvector() -> Check:
    """The `vector` extension, not the Python package.

    A missing extension surfaces at the first embedding write as an opaque
    ProgrammingError deep in a bulk insert; surfacing it here instead is the
    difference between a readiness failure and a debugging afternoon.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            found = cursor.fetchone() is not None
    except Exception as exc:
        return Check("pgvector", False, required=True, detail=type(exc).__name__)
    return Check(
        "pgvector",
        found,
        required=True,
        detail="" if found else "CREATE EXTENSION vector has not run",
    )


def _check_embedder() -> Check:
    """Local ONNX embedder. Not required until it exists (M3)."""
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return Check(
            "embedder",
            False,
            required=False,
            detail="not installed until M3 (docs/PLAN.md §13)",
        )
    return Check("embedder", True, required=False)


def _check_anthropic_key() -> Check:
    present = bool(settings.ANTHROPIC_API_KEY)
    return Check(
        "anthropic_key",
        present,
        required=False,
        detail="" if present else "absent — the app runs in keyless mode",
    )


@api_view(["GET"])
def healthz(_request: Request) -> Response:
    """Liveness. Deliberately touches no dependency."""
    return Response({"status": "ok"})


@api_view(["GET"])
def readyz(_request: Request) -> Response:
    checks = [_check_database(), _check_pgvector(), _check_embedder(), _check_anthropic_key()]
    ready = all(check.ok for check in checks if check.required)
    return Response(
        {
            "status": "ready" if ready else "not_ready",
            "checks": {check.name: check.as_dict() for check in checks},
        },
        status=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@api_view(["GET"])
def version(_request: Request) -> Response:
    return Response(
        {
            "git_sha": os.environ.get("GIT_SHA", "dev"),
            "built_at": os.environ.get("BUILT_AT", "dev"),
            "model": settings.CIA_CHAT_MODEL,
            "embedding_model": settings.EMBEDDING_MODEL,
        }
    )
