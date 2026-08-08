"""Structure-aware chunking.

One splitter, one code path, one per-kind knob (the target size). An earlier
draft had two full policies with six hand-tuned constants; that was twice the
test surface for reasoning no measurement backed, so the eval's ablation in M4
is where a per-kind target earns or loses its keep.

**Atomic units.** The splitter never cuts through a bullet or a paragraph. On
these documents that is the whole game: a requirement bullet ("5+ years of
production Kubernetes") is a self-contained claim, and half of one retrieves as
noise. Sliding-window chunking — the default RAG recipe, designed for long
unstructured prose — would slice straight through them.

**Why the résumé target is larger.** A résumé bullet is 15–40 tokens and a full
role block (title, employer, dates, 4–5 bullets) is 120–220. A 320-token target
keeps *one role* intact, which is the unit a hiring question resolves against,
without averaging two employers into one vector. JD requirement bullets are
short, dense and near-independent, so a 256-token target keeps them from being
averaged into a single vector that matches everything weakly.

**The invariant.** For every chunk this module emits:

    normalized_text[chunk.char_start:chunk.char_end] == chunk.text

Property-tested in tests/unit/test_chunking.py. The entire evidence-panel
feature is that one assertion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from apps.documents.chunking.sections import ROLE_BOUNDARY_RE, Section, SectionKind
from apps.documents.chunking.tokenizer import (
    MAX_SEQUENCE_TOKENS,
    count_tokens,
    truncate_to_tokens,
)

# Per-kind target. Everything else below is shared.
TARGET_TOKENS = {"resume": 320, "job": 256}
DEFAULT_TARGET_TOKENS = 320

# Hard ceiling for a raw chunk. Deliberately below the encoder's 512 so the
# breadcrumb (<=48 tokens) still fits inside the limit — see breadcrumb.py.
MAX_CHUNK_TOKENS = 448
# Only used when a single atomic unit is itself oversized and must be split.
OVERLAP_TOKENS = 64
# Below this a chunk is merged forward; a 12-token fragment is not retrievable.
MIN_CHUNK_TOKENS = 40

_BLANK_LINE = re.compile(r"\n[ \t]*\n")


@dataclass(frozen=True)
class Chunk:
    """A span of `normalized_text`, plus the section it came from."""

    text: str
    char_start: int
    char_end: int
    ordinal: int
    section_index: int
    token_count: int

    @property
    def leading_line(self) -> str:
        return self.text.lstrip().split("\n", 1)[0]


@dataclass(frozen=True)
class _Unit:
    """An atomic span — a bullet or a paragraph. Never split unless oversized."""

    char_start: int
    char_end: int
    # A boundary that must not be merged across, whatever the token budget says.
    # Used for role headers inside EXPERIENCE: a section holding two employers
    # can easily fit under the target, and packing them together produces one
    # vector that is the average of two jobs and precisely describes neither.
    hard_break: bool = False


def split_document(
    normalized_text: str,
    sections: list[Section],
    *,
    kind: str,
) -> list[Chunk]:
    """Chunk a document, one section at a time.

    Chunks never straddle a section boundary. That costs a few extra short
    chunks and buys two things worth more: every chunk has exactly one
    breadcrumb, and boilerplate exclusion stays exact — a chunk cannot be half
    requirements and half EEO text.
    """
    if not normalized_text.strip():
        return []

    target = TARGET_TOKENS.get(kind, DEFAULT_TARGET_TOKENS)
    chunks: list[Chunk] = []

    for section_index, section in enumerate(sections):
        for span in _split_section(normalized_text, section, target=target):
            text = normalized_text[span.char_start : span.char_end]
            chunks.append(
                Chunk(
                    text=text,
                    char_start=span.char_start,
                    char_end=span.char_end,
                    ordinal=len(chunks),
                    section_index=section_index,
                    token_count=count_tokens(text),
                )
            )

    return chunks


def _split_section(text: str, section: Section, *, target: int) -> list[_Unit]:
    """Pack a section's atomic units into target-sized spans."""
    units = _atomic_units(text, section)
    if not units:
        return []

    packed: list[_Unit] = []
    current_start: int | None = None
    current_end: int | None = None

    for unit in units:
        oversized = _split_oversized(text, unit)
        for index, piece in enumerate(oversized):
            piece_tokens = count_tokens(text[piece.char_start : piece.char_end])

            if current_start is None:
                current_start, current_end = piece.char_start, piece.char_end
                continue

            # A role header closes whatever was open, regardless of size.
            if unit.hard_break and index == 0:
                packed.append(_Unit(current_start, current_end or piece.char_start))
                current_start, current_end = piece.char_start, piece.char_end
                continue

            combined_tokens = count_tokens(text[current_start : piece.char_end])
            if combined_tokens <= target or piece_tokens < MIN_CHUNK_TOKENS:
                # Merging a runt forward even past the target is deliberate: a
                # 20-token orphan chunk retrieves badly and pollutes top-k.
                if combined_tokens <= MAX_CHUNK_TOKENS:
                    current_end = piece.char_end
                    continue

            packed.append(_Unit(current_start, current_end or piece.char_start))
            current_start, current_end = piece.char_start, piece.char_end

    if current_start is not None and current_end is not None:
        packed.append(_Unit(current_start, current_end))

    return [unit for unit in packed if text[unit.char_start : unit.char_end].strip()]


def _atomic_units(text: str, section: Section) -> list[_Unit]:
    """Break a section into bullets and paragraphs, preserving offsets.

    Offsets are tracked by construction rather than recovered with `.find()`:
    searching for a substring finds the *first* occurrence, and "- Strong Go."
    can legitimately appear twice in one document.
    """
    body = text[section.char_start : section.char_end]
    units: list[_Unit] = []

    cursor = 0
    for block in _BLANK_LINE.split(body):
        block_start = body.index(block, cursor) if block else cursor
        cursor = block_start + len(block)

        if not block.strip():
            continue

        units.extend(
            _bullet_units(
                body,
                block,
                block_start,
                section.char_start,
                mark_role_boundaries=section.kind == SectionKind.EXPERIENCE,
            )
        )

    return units


def _is_role_header(line: str) -> bool:
    """ "Senior Backend Engineer, Meridian Logistics — 2022 to Present"."""
    stripped = line.strip()
    return (
        bool(stripped) and not stripped.startswith("- ") and bool(ROLE_BOUNDARY_RE.search(stripped))
    )


def _bullet_units(
    body: str,
    block: str,
    block_start: int,
    base: int,
    *,
    mark_role_boundaries: bool = False,
) -> list[_Unit]:
    """Split a paragraph block into bullets, or return it whole."""
    lines = block.split("\n")
    if not any(line.lstrip().startswith("- ") for line in lines):
        return [
            _Unit(
                base + block_start,
                base + block_start + len(block),
                hard_break=mark_role_boundaries and _is_role_header(lines[0]),
            )
        ]

    units: list[_Unit] = []
    offset = block_start
    current_start: int | None = None
    current_is_role = False

    for line in lines:
        line_start = offset
        offset += len(line) + 1  # +1 for the newline consumed by split

        if line.lstrip().startswith("- "):
            if current_start is not None:
                units.append(
                    _Unit(base + current_start, base + line_start, hard_break=current_is_role)
                )
            current_start = line_start
            current_is_role = False
        elif current_start is None:
            # Text before the first bullet — on a résumé this is the role
            # header the bullets below belong to, which is exactly where the
            # chunk boundary has to fall.
            current_start = line_start
            current_is_role = mark_role_boundaries and _is_role_header(line)

    if current_start is not None:
        units.append(
            _Unit(
                base + current_start,
                base + block_start + len(block),
                hard_break=current_is_role,
            )
        )

    return units


def _split_oversized(text: str, unit: _Unit) -> list[_Unit]:
    """Split a single atomic unit that exceeds the hard ceiling.

    Reached only by pathological input — a requirements "bullet" that is really
    six merged paragraphs, or a table flattened into one line. Overlap applies
    *here only*: within a normal section, consecutive chunks share no text,
    because duplicated spans mean duplicated citations pointing at the same
    characters.
    """
    body = text[unit.char_start : unit.char_end]
    if count_tokens(body) <= MAX_CHUNK_TOKENS:
        return [unit]

    pieces: list[_Unit] = []
    cursor = unit.char_start

    while cursor < unit.char_end:
        window = text[cursor : unit.char_end]
        head = truncate_to_tokens(window, MAX_CHUNK_TOKENS)
        if not head:
            break

        end = cursor + len(head)
        # Prefer a sentence or line boundary near the cut so a chunk does not
        # start mid-clause.
        boundary = max(head.rfind("\n"), head.rfind(". "))
        if boundary > len(head) // 2:
            end = cursor + boundary + 1

        pieces.append(_Unit(cursor, min(end, unit.char_end)))

        if end >= unit.char_end:
            break

        overlap_text = truncate_to_tokens(text[cursor:end][::-1], OVERLAP_TOKENS)[::-1]
        cursor = max(cursor + 1, end - len(overlap_text))

    return pieces or [unit]


def assert_fits_encoder(embed_text: str) -> None:
    """Hard guard against silent encoder truncation.

    Called by the ingest pipeline for every chunk. A chunk whose embed_text
    exceeds 512 tokens has its tail dropped by bge-small with no error, so this
    raises rather than logs — a retrieval bug with no symptom is the worst kind.
    """
    tokens = count_tokens(embed_text)
    if tokens > MAX_SEQUENCE_TOKENS:
        raise ValueError(
            f"embed_text is {tokens} tokens, over the encoder's "
            f"{MAX_SEQUENCE_TOKENS}-token limit; its tail would be silently dropped"
        )
