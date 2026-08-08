"""Match extracted requirements against résumé evidence.

The keyless half of gap analysis. For each requirement, look for the skill (and
its aliases) in the résumé's chunks:

* **strong**  — evidenced in an experience/project section, i.e. the candidate
                describes having *done* it
* **partial** — present only in a skills list, i.e. claimed but unevidenced
* **missing** — absent

That distinction is the whole point of not collapsing this to a boolean. "Listed
Kubernetes under Skills" and "ran Kubernetes clusters in production for three
years" are different claims, and a hiring manager treats them differently. A
binary has/hasn't would report both as a match and quietly overstate the
candidate.

Blunter than an LLM reading for meaning, and honest about it: this is lexical
presence, not comprehension. The eval publishes the gap between the two.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from apps.analysis.extractors.base import ExtractedRequirement
from apps.analysis.models import ExtractorSource, MatchStatus
from apps.documents.chunking.sections import SectionKind
from apps.rag.aliases import expand

# Sections where a mention means the candidate *did* the thing, rather than
# merely listed it.
_EVIDENCE_SECTIONS = frozenset(
    {
        SectionKind.EXPERIENCE,
        SectionKind.PROJECTS,
        SectionKind.SUMMARY,
        SectionKind.CERTIFICATIONS,
    }
)
_CLAIM_SECTIONS = frozenset({SectionKind.SKILLS, SectionKind.EDUCATION, SectionKind.OTHER})


@dataclass(frozen=True)
class ResumeChunk:
    """The minimum a matcher needs. Keeps this module free of ORM imports."""

    chunk_id: str
    text: str
    section_kind: str


@dataclass(frozen=True)
class MatchResult:
    status: MatchStatus
    rationale: str
    evidence_chunk_ids: list[str]
    confidence: float
    source: str = ExtractorSource.DETERMINISTIC


def match_requirement(
    requirement: ExtractedRequirement, resume_chunks: list[ResumeChunk]
) -> MatchResult:
    """Classify one requirement against the résumé."""
    surfaces = expand(requirement.skill)
    if not surfaces:
        return MatchResult(
            status=MatchStatus.MISSING,
            rationale="No recognisable skill in this requirement.",
            evidence_chunk_ids=[],
            confidence=0.2,
        )

    evidence: list[str] = []
    claims: list[str] = []

    for chunk in resume_chunks:
        if not _mentions(chunk.text, surfaces):
            continue
        if chunk.section_kind in _EVIDENCE_SECTIONS:
            evidence.append(chunk.chunk_id)
        elif chunk.section_kind in _CLAIM_SECTIONS:
            claims.append(chunk.chunk_id)
        else:
            claims.append(chunk.chunk_id)

    if evidence:
        return MatchResult(
            status=MatchStatus.STRONG,
            rationale=f"{requirement.skill} appears in your experience, not just your skills list.",
            evidence_chunk_ids=evidence[:3],
            confidence=0.75,
        )
    if claims:
        return MatchResult(
            status=MatchStatus.PARTIAL,
            rationale=(
                f"{requirement.skill} is listed but no role describes using it — "
                "worth adding evidence."
            ),
            evidence_chunk_ids=claims[:3],
            confidence=0.5,
        )
    return MatchResult(
        status=MatchStatus.MISSING,
        rationale=f"No mention of {requirement.skill} anywhere in your résumé.",
        evidence_chunk_ids=[],
        confidence=0.6,
    )


def _mentions(text: str, surfaces: set[str]) -> bool:
    """Whole-token match against any surface form.

    Word-boundary anchored rather than substring: a plain `in` check reports
    "go" as present in "Django", "algorithms" and "category", which would mark a
    Python-only candidate as matching a Go requirement.
    """
    lowered = text.lower()
    return any(re.search(rf"(?<!\w){re.escape(surface)}(?!\w)", lowered) for surface in surfaces)
