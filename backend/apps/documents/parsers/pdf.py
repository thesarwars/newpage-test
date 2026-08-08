"""PDF extraction, with invisible-text detection.

pdfplumber rather than pypdf or pypdfium2 for one reason: it exposes per-
character `size` and `non_stroking_color`, which is the only way to catch the
oldest trick in the résumé-screening book — instructions rendered in white on a
white background, or at 0.5pt. A human reviewer sees nothing; the text layer,
and therefore the model, sees everything.

Slower than the alternatives, and irrelevant at a 30-page cap.
"""

from __future__ import annotations

from typing import Any, BinaryIO, cast

import pdfplumber
import structlog

from apps.documents.parsers.base import ParsedDocument
from apps.documents.sanitize import (
    HIDDEN_TEXT_CONTRAST_EPSILON,
    HIDDEN_TEXT_MIN_FONT_SIZE,
)
from apps.documents.validators import EncryptedPdfError, ParseFailedError

log = structlog.get_logger(__name__)

# Below this many consecutive hidden characters it is a rendering artefact
# (a stray glyph, a watermark remnant), not a payload.
_MIN_HIDDEN_RUN = 12


def parse_pdf(handle: BinaryIO) -> ParsedDocument:
    handle.seek(0)
    try:
        # pdfplumber's stub declares a narrower union (BufferedReader | BytesIO |
        # str | Path) than what it actually accepts, which is any seekable
        # binary stream — including Django's UploadedFile. Cast rather than
        # widen the parser signature, so the Parser protocol stays honest.
        with pdfplumber.open(cast("Any", handle)) as pdf:
            pages = pdf.pages
            page_texts: list[str] = []
            hidden_spans: list[str] = []

            for page in pages:
                page_texts.append(page.extract_text() or "")
                hidden_spans.extend(_hidden_runs(page.chars))

            return ParsedDocument(
                text="\n\n".join(page_texts),
                page_count=len(pages),
                hidden_spans=hidden_spans,
            )
    except Exception as exc:
        if _looks_encrypted(exc):
            raise EncryptedPdfError() from exc
        log.info("pdf_parse_failed", exc_type=type(exc).__name__)
        raise ParseFailedError() from exc


def _looks_encrypted(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "encrypt" in text or "password" in text


def _hidden_runs(chars: list[dict[str, Any]]) -> list[str]:
    """Find consecutive runs of characters a reader cannot see."""
    runs: list[str] = []
    current: list[str] = []

    for char in chars:
        if _is_hidden(char):
            current.append(str(char.get("text", "")))
            continue
        if len(current) >= _MIN_HIDDEN_RUN:
            runs.append("".join(current))
        current = []

    if len(current) >= _MIN_HIDDEN_RUN:
        runs.append("".join(current))

    return runs


def _is_hidden(char: dict[str, Any]) -> bool:
    size = char.get("size")
    if isinstance(size, int | float) and size < HIDDEN_TEXT_MIN_FONT_SIZE:
        return True

    return _is_background_coloured(char.get("non_stroking_color"))


def _is_background_coloured(colour: Any) -> bool:
    """True when the glyph colour is indistinguishable from a white page.

    pdfplumber reports colour in whatever space the PDF used — a scalar for
    greyscale, a 3-tuple for RGB, a 4-tuple for CMYK. Anything unrecognised is
    treated as visible: a false negative here costs a missed flag, while a false
    positive would quarantine legitimate text.
    """
    if colour is None:
        return False

    if isinstance(colour, int | float):
        return float(colour) >= 1.0 - HIDDEN_TEXT_CONTRAST_EPSILON

    if isinstance(colour, list | tuple):
        values = [float(component) for component in colour if isinstance(component, int | float)]
        if len(values) == 1:
            return values[0] >= 1.0 - HIDDEN_TEXT_CONTRAST_EPSILON
        if len(values) == 3:
            return all(component >= 1.0 - HIDDEN_TEXT_CONTRAST_EPSILON for component in values)
        if len(values) == 4:
            # CMYK: all-zero ink is white.
            return all(component <= HIDDEN_TEXT_CONTRAST_EPSILON for component in values)

    return False
