"""The ingest pipeline.

    validate -> parse -> normalize -> scan -> sections -> ready

Synchronous, and that is a decision rather than an omission: the intake caps
(10 MB, 30 pages) bound this at a few seconds, and a broker would add two
stateful services, a worker image, and the entire "stuck in parsing forever"
failure class in exchange for latency nobody would notice. The `TaskRunner`
seam exists for when that stops being true (docs/PLAN.md §2, §16).

Chunking and embedding join the chain in M3, which is why `status` already has
those states.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO

import structlog
from django.db import transaction

from apps.core.logging import log_safe
from apps.core.models import Session
from apps.documents.chunking.sections import detect_sections
from apps.documents.models import Document, DocumentKind, DocumentStatus, Section, next_ordinal
from apps.documents.normalize import normalize
from apps.documents.parsers.base import ParsedDocument
from apps.documents.parsers.docx import parse_docx
from apps.documents.parsers.pdf import parse_pdf
from apps.documents.parsers.plain import parse_plain
from apps.documents.sanitize import scan
from apps.documents.validators import (
    MAX_PAGES,
    UploadError,
    validate_extracted_text,
    validate_pasted_text,
    validate_upload,
)

log = structlog.get_logger(__name__)

_PARSERS = {
    "pdf": parse_pdf,
    "docx": parse_docx,
    "txt": parse_plain,
    "md": parse_plain,
}

_MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
}


@dataclass(frozen=True)
class IngestResult:
    document: Document
    sections: list[Section]


class TooManyJobsError(UploadError):
    error_code = "too_many_jobs"
    message = "You've reached the limit of 10 job descriptions."
    hint = "Delete one you're no longer considering to add another."


class ResumeAlreadyExistsError(UploadError):
    error_code = "resume_exists"
    message = "This workspace already has a résumé."
    hint = "Delete the current one first — comparing two résumés isn't supported yet."


def ingest_upload(
    *,
    session: Session,
    kind: str,
    filename: str,
    size_bytes: int,
    handle: BinaryIO,
    label: str = "",
) -> IngestResult:
    """Validate, parse and store an uploaded file."""
    validated = validate_upload(filename=filename, size_bytes=size_bytes, handle=handle)
    parsed = _PARSERS[validated.extension](handle)

    return _persist(
        session=session,
        kind=kind,
        parsed=parsed,
        label=label,
        original_filename=filename,
        mime_type=_MIME_TYPES[validated.extension],
        size_bytes=size_bytes,
        validate_text=lambda text: validate_extracted_text(text, page_count=parsed.page_count),
    )


def ingest_text(*, session: Session, kind: str, text: str, label: str = "") -> IngestResult:
    """Ingest pasted text.

    Not a convenience. Every parse failure this module raises points the user at
    "paste the text instead", and a 422 that names a fallback with nowhere to
    perform it is a dead end on the first scanned PDF.
    """
    parsed = ParsedDocument(text=text, page_count=max(1, len(text) // 1800 + 1))
    return _persist(
        session=session,
        kind=kind,
        parsed=parsed,
        label=label,
        original_filename="",
        mime_type="text/plain",
        size_bytes=len(text.encode()),
        validate_text=validate_pasted_text,
    )


@transaction.atomic
def _persist(
    *,
    session: Session,
    kind: str,
    parsed: ParsedDocument,
    label: str,
    original_filename: str,
    mime_type: str,
    size_bytes: int,
    validate_text: Callable[[str], None],
) -> IngestResult:
    _enforce_session_quota(session, kind)
    validate_text(parsed.text)

    normalized = normalize(parsed.text)

    # Re-checked after normalization: a PDF can extract 300 characters of
    # whitespace and control codes and collapse to nothing useful.
    validate_text(normalized.text)

    findings = scan(
        normalized.text,
        invisible_chars_removed=normalized.invisible_chars_removed,
        hidden_spans=parsed.hidden_spans,
    )

    document = Document.objects.create(
        session=session,
        kind=kind,
        ordinal=next_ordinal(session, kind),
        label=label.strip()[:200],
        original_filename=original_filename[:255],
        mime_type=mime_type,
        size_bytes=size_bytes,
        page_count=min(parsed.page_count, MAX_PAGES),
        normalized_text=normalized.text,
        text_sha256=hashlib.sha256(normalized.text.encode()).hexdigest(),
        injection_flag=findings.flagged,
        injection_reasons=findings.reasons,
        status=DocumentStatus.READY,
    )

    sections = Section.objects.bulk_create(
        Section(
            session=session,
            document=document,
            heading=detected.heading[:200],
            kind=detected.kind.value,
            char_start=detected.char_start,
            char_end=detected.char_end,
            is_boilerplate=detected.is_boilerplate,
            order=detected.order,
        )
        for detected in detect_sections(normalized.text)
    )

    log.info(
        "document_ingested",
        sections=len(sections),
        injection_flag=findings.flagged,
        injection_reasons=findings.reasons,
        **log_safe(document),
    )

    return IngestResult(document=document, sections=list(sections))


def _enforce_session_quota(session: Session, kind: str) -> None:
    existing = Document.objects.for_session(session).filter(kind=kind).count()
    if kind == DocumentKind.RESUME and existing >= 1:
        raise ResumeAlreadyExistsError()
    if kind == DocumentKind.JOB and existing >= 10:
        raise TooManyJobsError()
