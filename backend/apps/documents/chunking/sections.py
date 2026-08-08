"""Structure detection over normalized text.

Résumés and job descriptions are *structured* documents pretending to be prose.
A résumé has EXPERIENCE and SKILLS; a JD has REQUIREMENTS and BENEFITS. Throwing
that structure away and chunking on a sliding window — the default RAG recipe,
designed for long unstructured prose — discards the most useful retrieval signal
these documents have.

Two things come out of this module and both matter downstream:

* **Breadcrumbs.** A chunk that reads "Reduced p99 latency 40%" carries no signal
  about *which employer*. The section heading supplies it (M3).
* **Boilerplate flags.** A job description is routinely 30-40% benefits and EEO
  text. Embedding it dilutes the index and it *will* surface on "what does this
  company value". Flagged here, excluded from retrieval, and shown in the UI as
  a visible decision rather than silent data loss.

All offsets index into `normalized_text` (apps/documents/normalize.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class SectionKind(StrEnum):
    # Résumé
    SUMMARY = "summary"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    SKILLS = "skills"
    PROJECTS = "projects"
    CERTIFICATIONS = "certifications"
    # Job description
    ABOUT = "about"
    RESPONSIBILITIES = "responsibilities"
    REQUIREMENTS = "requirements"
    NICE_TO_HAVE = "nice_to_have"
    BENEFITS = "benefits"
    LEGAL = "legal"
    OTHER = "other"


# Sections whose content is real but never relevant to candidate-job fit.
# Excluded from retrieval by default; surfaced in the UI as "3 chunks skipped".
BOILERPLATE_KINDS = frozenset({SectionKind.BENEFITS, SectionKind.LEGAL})

# Ordered: the first pattern to match wins, so the more specific ones lead.
_HEADING_PATTERNS: list[tuple[SectionKind, re.Pattern[str]]] = [
    # First, and anchored: a heading that *starts* with "About" is an overview,
    # whatever follows. Without this, "ABOUT THE ROLE" matched the
    # RESPONSIBILITIES pattern below (which lists "the role") and a JD's company
    # blurb was indexed as its duties list.
    (SectionKind.ABOUT, re.compile(r"^\s*about\b", re.I)),
    (
        SectionKind.NICE_TO_HAVE,
        re.compile(r"\b(nice[\s-]?to[\s-]?have|preferred|bonus|plus(es)?)\b", re.I),
    ),
    (
        SectionKind.REQUIREMENTS,
        re.compile(
            r"\b(requirements?|qualifications?|must[\s-]?haves?|what (you'?ll |we )?need|who you are)\b",
            re.I,
        ),
    ),
    (
        SectionKind.RESPONSIBILITIES,
        re.compile(
            r"\b(responsibilities|what you'?ll do|the role|duties|day[\s-]?to[\s-]?day)\b", re.I
        ),
    ),
    (
        SectionKind.BENEFITS,
        re.compile(r"\b(benefits?|perks?|compensation|what we offer|why join)\b", re.I),
    ),
    (
        SectionKind.LEGAL,
        re.compile(
            r"\b(eeo|equal opportunity|legal|disclaimer|privacy|e-verify|accommodations?)\b", re.I
        ),
    ),
    (
        SectionKind.EXPERIENCE,
        re.compile(
            r"\b(experience|employment|work history|professional background|career)\b", re.I
        ),
    ),
    (SectionKind.EDUCATION, re.compile(r"\b(education|academic|degrees?)\b", re.I)),
    (
        SectionKind.SKILLS,
        re.compile(
            r"\b(skills?|technical skills|technologies|tech stack|competencies|proficienc)\b", re.I
        ),
    ),
    (
        SectionKind.PROJECTS,
        re.compile(r"\b(projects?|portfolio|open source|publications?)\b", re.I),
    ),
    (SectionKind.CERTIFICATIONS, re.compile(r"\b(certifications?|licen[cs]es?|awards?)\b", re.I)),
    (SectionKind.SUMMARY, re.compile(r"\b(summary|profile|objective|about me)\b", re.I)),
    (
        SectionKind.ABOUT,
        re.compile(r"\b(about( us| the (company|team))?|who we are|our (mission|team))\b", re.I),
    ),
]

# A role boundary inside EXPERIENCE: "Senior Engineer, Acme  2021 - Present".
# Splitting on these keeps one employer per chunk, which is the unit a hiring
# question actually resolves against.
ROLE_BOUNDARY_RE = re.compile(
    r"\b(19|20)\d{2}\b.{0,20}?(?:–|—|-|to)\s*(?:present|current|\b(19|20)\d{2}\b)",
    re.I,
)

_MAX_HEADING_LENGTH = 60
_SENTENCE_END = (".", "!", "?", ",", ";")


@dataclass(frozen=True)
class Section:
    """A span of `normalized_text`, with its detected role."""

    heading: str
    kind: SectionKind
    char_start: int
    char_end: int
    order: int

    @property
    def is_boilerplate(self) -> bool:
        return self.kind in BOILERPLATE_KINDS


def looks_like_heading(line: str) -> bool:
    """Shape heuristic, applied before any vocabulary matching.

    Deliberately structural rather than a keyword list: a heading is short, has
    no terminal punctuation, and is visually set apart (ALL CAPS, Title Case, or
    trailing colon). Vocabulary alone would classify the sentence "I have five
    years of experience" as an EXPERIENCE heading.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > _MAX_HEADING_LENGTH:
        return False
    if stripped.endswith(_SENTENCE_END):
        return False
    if stripped.startswith("- "):
        return False

    if stripped.endswith(":"):
        return True

    letters = [c for c in stripped if c.isalpha()]
    if not letters:
        return False

    if all(c.isupper() for c in letters):
        return True

    words = [w for w in stripped.split() if any(c.isalpha() for c in w)]
    if len(words) <= 6 and all(w[0].isupper() for w in words if w[0].isalpha()):
        return True

    return False


def classify_heading(heading: str) -> SectionKind:
    for kind, pattern in _HEADING_PATTERNS:
        if pattern.search(heading):
            return kind
    return SectionKind.OTHER


def detect_sections(normalized_text: str) -> list[Section]:
    """Split `normalized_text` into contiguous, non-overlapping sections.

    Contiguity is a hard guarantee: every character of the document belongs to
    exactly one section, so no text can be silently dropped from the index by a
    heading heuristic that guessed wrong. Text before the first heading becomes
    an implicit leading section rather than being discarded — on a résumé that
    is usually the name and contact block, and on a JD the role title.
    """
    if not normalized_text.strip():
        return []

    boundaries = _heading_boundaries(normalized_text)

    if not boundaries:
        return [
            Section(
                heading="",
                kind=SectionKind.OTHER,
                char_start=0,
                char_end=len(normalized_text),
                order=0,
            )
        ]

    sections: list[Section] = []

    # Preamble: everything before the first heading.
    first_start = boundaries[0][0]
    if normalized_text[:first_start].strip():
        sections.append(
            Section(
                heading="",
                kind=SectionKind.SUMMARY,
                char_start=0,
                char_end=first_start,
                order=0,
            )
        )

    for index, (start, end_of_heading, heading) in enumerate(boundaries):
        next_start = (
            boundaries[index + 1][0] if index + 1 < len(boundaries) else len(normalized_text)
        )
        sections.append(
            Section(
                heading=heading,
                kind=classify_heading(heading),
                char_start=start,
                char_end=next_start,
                order=len(sections),
            )
        )
        del end_of_heading

    return sections


def _heading_boundaries(text: str) -> list[tuple[int, int, str]]:
    """Locate heading lines as (line_start, line_end, heading_text)."""
    boundaries: list[tuple[int, int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and looks_like_heading(line):
            boundaries.append((offset, offset + len(line), stripped.rstrip(":")))
        offset += len(line)
    return boundaries
