"""Parser contract.

Every parser returns raw text plus whatever it noticed that later stages cannot
see for themselves. The PDF parser is the interesting case: font size and colour
exist only while the page is open, so invisible-text detection has to happen
here and be *carried* to the scanner rather than re-derived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import BinaryIO, Protocol


@dataclass
class ParsedDocument:
    """Raw extracted text, pre-normalization."""

    text: str
    page_count: int = 1
    # Text that a human reading the rendered page would not see: near-zero font
    # size, or coloured to match the background. Passed to sanitize.scan().
    hidden_spans: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Parser(Protocol):
    def __call__(self, handle: BinaryIO) -> ParsedDocument: ...
