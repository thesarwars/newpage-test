"""Contextual prefixes, derived structurally rather than generated.

The single most common failure in résumé RAG: the bullet

    Reduced p99 latency from 1.4s to 380ms

carries no signal about *which employer*, *which document*, or even whether it
came from a résumé or a job posting. Retrieved on its own it is unattributable,
and the model cannot tell you whose achievement it is.

Anthropic's contextual-retrieval recipe fixes this by asking an LLM to write a
short context sentence for every chunk. That works and it is expensive: one call
per chunk, at ingest, for every document. The structure is already sitting in
the section headings we detected in M2, so deriving the prefix costs **zero
tokens and zero latency**:

    [Résumé — Experience — Senior Backend Engineer, Meridian Logistics (2022 to Present)]
    Reduced p99 latency from 1.4s to 380ms

Deliberately *not* claiming Anthropic's published 35–49% improvement figure for
this: that number is for the LLM-generated variant and does not transfer to a
structural approximation. What it buys here is measured by the eval in M4, and
the number that comes out is the one that goes in the README.

The prefix lands in `Chunk.embed_text` only. `Chunk.text` stays raw, so display
and `char_start`/`char_end` are untouched — the offset contract does not know
this module exists.
"""

from __future__ import annotations

import re

from apps.documents.chunking.tokenizer import count_tokens, truncate_to_tokens

# Budgeted *inside* the encoder's 512-token limit, not on top of it. A
# breadcrumb that pushed the chunk over the limit would silently truncate the
# chunk's tail — trading attribution for content, invisibly.
MAX_BREADCRUMB_TOKENS = 48

_ROLE_LINE = re.compile(r"^(?P<role>[^\n]{3,80}?)\s*(?:—|–|-|,)\s*(?P<rest>[^\n]{0,60})$")


def build(
    *,
    document_label: str,
    section_heading: str,
    leading_line: str = "",
) -> str:
    """Compose the bracketed prefix for a chunk.

    `leading_line` is the first line of the chunk itself, used to recover the
    role/employer inside a long EXPERIENCE section where the section heading
    alone ("Experience") says almost nothing.
    """
    parts = [part for part in (document_label.strip(), section_heading.strip()) if part]

    detail = _role_detail(leading_line)
    if detail and detail.lower() not in {p.lower() for p in parts}:
        parts.append(detail)

    if not parts:
        return ""

    breadcrumb = f"[{' — '.join(parts)}]"

    # Trim from the middle out rather than truncating: the document label and
    # the most specific part carry the most signal, and a breadcrumb ending in
    # "…" mid-word is worse than one with fewer components.
    while count_tokens(breadcrumb) > MAX_BREADCRUMB_TOKENS and len(parts) > 1:
        parts.pop(1) if len(parts) > 2 else parts.pop()
        breadcrumb = f"[{' — '.join(parts)}]"

    # Dropping components is not always enough — a single pathologically long
    # section heading can exceed the budget on its own, and the loop above stops
    # at one part. Truncating is the last resort, but the budget is a hard limit:
    # every token spent here is a token of chunk text the encoder cannot see.
    if count_tokens(breadcrumb) > MAX_BREADCRUMB_TOKENS:
        # -2 leaves room for the enclosing brackets.
        breadcrumb = f"[{truncate_to_tokens(parts[0], MAX_BREADCRUMB_TOKENS - 2).rstrip()}]"

    return breadcrumb


def apply(breadcrumb: str, text: str) -> str:
    """The string actually handed to the encoder."""
    return f"{breadcrumb}\n{text}" if breadcrumb else text


def _role_detail(leading_line: str) -> str:
    """Pull a role/employer descriptor out of a chunk's first line.

    Only accepts lines that *look* like a role header — a bullet or a sentence
    would add noise rather than context.
    """
    line = leading_line.strip()
    if not line or line.startswith("- ") or line.endswith((".", "!", "?")):
        return ""
    if len(line) > 90:
        return ""

    match = _ROLE_LINE.match(line)
    return line if match else ""
