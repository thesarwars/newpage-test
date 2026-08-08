"""Plain text and Markdown.

Markdown is not rendered — the raw source *is* the document. Heading syntax
survives into normalized text, where section detection reads `## Requirements`
as a heading via the same shape rules it applies to a PDF.
"""

from __future__ import annotations

from typing import BinaryIO

from apps.documents.parsers.base import ParsedDocument
from apps.documents.validators import ParseFailedError

_CHARS_PER_PAGE_ESTIMATE = 1800


def parse_plain(handle: BinaryIO) -> ParsedDocument:
    handle.seek(0)
    raw = handle.read()

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Windows-exported .txt is routinely cp1252. Try it before giving up,
        # then fall back to lossy UTF-8 rather than rejecting a readable file
        # over a handful of smart quotes.
        try:
            text = raw.decode("cp1252")
        except UnicodeDecodeError as exc:
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception as inner:  # pragma: no cover - decode-with-replace cannot fail
                raise ParseFailedError() from inner
            del exc

    return ParsedDocument(
        text=text,
        page_count=max(1, len(text) // _CHARS_PER_PAGE_ESTIMATE + 1),
    )
