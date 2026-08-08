"""Document endpoints."""

from __future__ import annotations

from typing import Any

import structlog
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.errors import ApiError, NotFoundError, SessionRequiredError
from apps.core.models import Session
from apps.documents.ingest import ingest_text, ingest_upload
from apps.documents.models import Document, DocumentKind, Section, rail_order
from apps.documents.validators import MAX_FILE_BYTES

log = structlog.get_logger(__name__)


def _current(request: Request) -> Session:
    session: Session | None = getattr(request, "cia_session", None)
    if session is None:
        raise SessionRequiredError()
    return session


def _payload(request: Request) -> dict[str, Any]:
    """Narrow `request.data` to a mapping, once, at the boundary.

    DRF types it as `dict | list` because a JSON body can legally be an array.
    None of these endpoints accept one, so it is rejected here with a real error
    rather than exploding on `.get` somewhere deeper.
    """
    data = request.data
    if not isinstance(data, dict):
        raise ApiError(
            "Expected a JSON object.",
            error_code="invalid_body",
            hint="Send an object with the documented fields, not an array.",
        )
    return data


def _validate_kind(raw: Any) -> str:
    if raw not in DocumentKind.values:
        raise ApiError(
            "Documents must be uploaded as either a résumé or a job description.",
            error_code="invalid_kind",
            hint="Set `kind` to 'resume' or 'job'.",
        )
    return str(raw)


def _serialize_section(section: Section) -> dict[str, Any]:
    return {
        "id": str(section.id),
        "heading": section.heading,
        "kind": section.kind,
        "char_start": section.char_start,
        "char_end": section.char_end,
        "is_boilerplate": section.is_boilerplate,
    }


def _serialize(document: Document, *, include_text: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(document.id),
        "kind": document.kind,
        "ordinal": document.ordinal,
        "label": document.display_label,
        "company": document.company,
        "original_filename": document.original_filename,
        "page_count": document.page_count,
        "size_bytes": document.size_bytes,
        "status": document.status,
        "error_code": document.error_code,
        "injection_flag": document.injection_flag,
        "injection_reasons": document.injection_reasons,
        "sections": [_serialize_section(s) for s in document.sections.all()],
        "created_at": document.created_at.isoformat(),
    }
    if include_text:
        # The evidence panel's data source. Immutable once ready, so the client
        # caches it indefinitely and citation offsets index straight into it.
        payload["normalized_text"] = document.normalized_text
    return payload


@api_view(["GET", "POST"])
@parser_classes([MultiPartParser, FormParser])
def documents(request: Request) -> Response:
    session = _current(request)

    if request.method == "GET":
        queryset = rail_order(Document.objects.for_session(session).prefetch_related("sections"))
        return Response({"documents": [_serialize(doc) for doc in queryset]})

    upload = request.FILES.get("file")
    if upload is None:
        raise ApiError(
            "No file was included in the upload.",
            error_code="no_file",
            hint="Attach a PDF, DOCX, TXT or Markdown file.",
        )

    body = _payload(request)
    kind = _validate_kind(body.get("kind"))

    result = ingest_upload(
        session=session,
        kind=kind,
        filename=upload.name or "upload",
        size_bytes=upload.size or 0,
        handle=upload.file,
        label=str(body.get("label", "")),
    )
    return Response(_serialize(result.document), status=status.HTTP_201_CREATED)


@api_view(["POST"])
def paste(request: Request) -> Response:
    """The fallback every parse-failure hint points at.

    Not optional: a 422 that says "paste the text instead" with nowhere to paste
    is a dead end the first time someone uploads a scanned résumé.
    """
    session = _current(request)
    body = _payload(request)
    kind = _validate_kind(body.get("kind"))
    text = str(body.get("text", ""))

    if len(text.encode()) > MAX_FILE_BYTES:
        raise ApiError(
            "That's more text than this accepts.",
            error_code="too_large",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    result = ingest_text(
        session=session,
        kind=kind,
        text=text,
        label=str(body.get("label", "")),
    )
    return Response(_serialize(result.document), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
def document_detail(request: Request, document_id: str) -> Response:
    session = _current(request)

    # for_session first, always. A document belonging to another session must be
    # indistinguishable from one that does not exist — same 404, same body.
    document = (
        Document.objects.for_session(session)
        .prefetch_related("sections")
        .filter(pk=document_id)
        .first()
    )
    if document is None:
        raise NotFoundError()

    if request.method == "GET":
        return Response(_serialize(document, include_text=True))

    if request.method == "PATCH":
        body = _payload(request)
        if "label" in body:
            document.label = str(body["label"]).strip()[:200]
        if "company" in body:
            document.company = str(body["company"]).strip()[:200]
        document.save(update_fields=["label", "company", "updated_at"])
        return Response(_serialize(document))

    return _delete(document)


@transaction.atomic
def _delete(document: Document) -> Response:
    session = document.session
    kind = document.kind
    document_id = str(document.id)

    # No blob to unlink: the upload is never written to disk (see models.py).
    document.delete()

    _renumber(session, kind)
    log.info("document_deleted", document_id=document_id, kind=kind)

    return Response(status=status.HTTP_204_NO_CONTENT)


def _renumber(session: Session, kind: str) -> None:
    """Close the gap left by a deletion.

    "Job #2" is a user-facing handle *and* a retrieval filter. Leaving a hole
    means the third job is labelled #4, and the next upload would collide with
    the unique constraint. Renumbered in two passes because that constraint is
    enforced per statement.
    """
    if kind != DocumentKind.JOB:
        return

    remaining = list(Document.objects.for_session(session).filter(kind=kind).order_by("ordinal"))

    for offset, document in enumerate(remaining, start=1):
        document.ordinal = 1000 + offset
    Document.objects.bulk_update(remaining, ["ordinal"])

    for offset, document in enumerate(remaining, start=1):
        document.ordinal = offset
    Document.objects.bulk_update(remaining, ["ordinal"])
