"""The requirement-extraction seam.

Two implementations: deterministic (regex over requirement bullets, no API key)
and LLM (M8). The protocol is what makes the second an upgrade rather than a
prerequisite.

This is the single highest-leverage decision in the build. An LLM-only extractor
means that a reviewer who does the most predictable thing — upload their own
résumé and a real posting — gets a working document rail and evidence panel next
to an **empty** Fit Board and an **empty** Gap Matrix, with no explanation.
Roughly 80 lines of regex makes the whole product work with no key at all, and
turns "vector search cannot retrieve absence" from a claim in a README into a
running demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from apps.documents.chunking.sections import Section

from apps.analysis.models import RequirementCategory


@dataclass(frozen=True)
class ExtractedRequirement:
    """One requirement, before it becomes a row."""

    text: str
    skill: str
    category: RequirementCategory
    must_have: bool
    char_start: int
    char_end: int


class RequirementExtractor(Protocol):
    @property
    def source(self) -> str:
        """Which implementation produced these — stored on every row."""
        ...

    def extract(
        self, *, normalized_text: str, sections: list[Section]
    ) -> list[ExtractedRequirement]: ...
