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
from django.contrib.postgres.search import SearchVector
from django.db import transaction

from apps.core.logging import log_safe
from apps.core.models import Session
from apps.documents.chunking.breadcrumb import apply as apply_breadcrumb
from apps.documents.chunking.breadcrumb import build as build_breadcrumb
from apps.documents.chunking.sections import Section as DetectedSection
from apps.documents.chunking.sections import detect_sections
from apps.documents.chunking.splitter import assert_fits_encoder, split_document
from apps.documents.models import (
    Chunk,
    Document,
    DocumentKind,
    DocumentStatus,
    Section,
    next_ordinal,
)
from apps.documents.normalize import normalize
from apps.documents.parsers.base import ParsedDocument
from apps.documents.parsers.docx import parse_docx
from apps.documents.parsers.pdf import parse_pdf
from apps.documents.parsers.plain import parse_plain
from apps.documents.sanitize import ScanResult, scan
from apps.documents.validators import (
    MAX_PAGES,
    UploadError,
    validate_extracted_text,
    validate_pasted_text,
    validate_upload,
)
from apps.rag.embeddings import get_embedder

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
    chunks: list[Chunk]


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
        status=DocumentStatus.PARSING,
    )

    detected_sections = detect_sections(normalized.text)
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
        # detected_sections, not a second detect_sections() call: chunks carry a
        # positional section_index into this exact list, and two independently
        # produced lists would be a silent misalignment waiting for the day the
        # detector becomes non-deterministic.
        for detected in detected_sections
    )

    chunks = _build_chunks(
        document=document,
        normalized_text=normalized.text,
        detected_sections=detected_sections,
        sections=list(sections),
        injection_findings=findings,
    )

    log.info(
        "document_ingested",
        sections=len(sections),
        chunks=len(chunks),
        injection_flag=findings.flagged,
        injection_reasons=findings.reasons,
        **log_safe(document),
    )

    return IngestResult(document=document, sections=list(sections), chunks=chunks)


def _build_chunks(
    *,
    document: Document,
    normalized_text: str,
    detected_sections: list[DetectedSection],
    sections: list[Section],
    injection_findings: ScanResult,
) -> list[Chunk]:
    """Chunk, embed and index a document in one transaction with its rows.

    Embedding happens inline. At the intake caps this is a couple of seconds for
    a few hundred chunks, and the alternative — a broker, a worker, and a
    partially-embedded document that retrieval has to reason about — is a much
    larger problem than the latency it removes.
    """
    document.status = DocumentStatus.CHUNKING
    document.save(update_fields=["status", "updated_at"])

    split = split_document(normalized_text, detected_sections, kind=document.kind)
    if not split:
        return []

    flagged_spans = [(f.char_start, f.char_end) for f in injection_findings.findings]

    rows: list[Chunk] = []
    for piece in split:
        detected = detected_sections[piece.section_index]
        breadcrumb = build_breadcrumb(
            document_label=document.display_label,
            section_heading=detected.heading,
            leading_line=piece.leading_line,
        )
        embed_text = apply_breadcrumb(breadcrumb, piece.text)
        assert_fits_encoder(embed_text)

        rows.append(
            Chunk(
                session=document.session,
                document=document,
                section=sections[piece.section_index] if sections else None,
                ordinal=piece.ordinal,
                text=piece.text,
                char_start=piece.char_start,
                char_end=piece.char_end,
                embed_text=embed_text,
                token_count=piece.token_count,
                is_boilerplate=detected.is_boilerplate,
                injection_flag=_overlaps_any(piece.char_start, piece.char_end, flagged_spans),
            )
        )

    document.status = DocumentStatus.EMBEDDING
    document.save(update_fields=["status", "updated_at"])

    embedder = get_embedder()
    vectors = embedder.embed_passages([row.embed_text for row in rows])
    for row, vector in zip(rows, vectors, strict=True):
        row.embedding = vector

    created = Chunk.objects.bulk_create(rows)

    # search_vector is populated in a second statement rather than per row:
    # to_tsvector must run in the database, and one UPDATE beats N round trips.
    Chunk.objects.filter(document=document).update(
        search_vector=SearchVector("embed_text", config="english")
    )

    document.status = DocumentStatus.READY
    document.embedding_model = embedder.name
    document.save(update_fields=["status", "embedding_model", "updated_at"])

    return list(created)


def _overlaps_any(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    """Does this chunk intersect a span the injection scanner flagged?

    Chunk-level rather than document-level: quarantining a whole job description
    because one line looked suspicious would discard the requirements the user
    actually needs. Only the offending chunks leave the index.
    """
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _enforce_session_quota(session: Session, kind: str) -> None:
    existing = Document.objects.for_session(session).filter(kind=kind).count()
    if kind == DocumentKind.RESUME and existing >= 1:
        raise ResumeAlreadyExistsError()
    if kind == DocumentKind.JOB and existing >= 10:
        raise TooManyJobsError()
