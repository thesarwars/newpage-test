"""The canonical text contract.

`normalize()` produces `Document.normalized_text` — the single string that every
`char_start` / `char_end` in this system indexes into. Sections index into it,
chunks index into it, and Anthropic's `char_location` citation offsets are
rebased onto it. If this function is not deterministic and idempotent, the
evidence panel highlights the wrong span and there is no error to notice.

It runs *before* anything computes an offset, so it is free to change the length
of the text (de-hyphenation does). Nothing downstream may modify the string
again — that is the whole discipline.

The invariant, property-tested in M3 once chunks exist:

    normalized_text[c.char_start:c.char_end] == c.text
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Zero-width and bidirectional-override characters. Stripped because they are
# invisible to a reader but not to a tokenizer, which makes them a way to hide
# instructions inside a job description. `scan()` in sanitize.py is told how many
# were removed so the removal is *reported*, not silent.
ZERO_WIDTH = "​‌‍﻿⁠"
BIDI_CONTROLS = "‪‫‬‭‮⁦⁧⁨⁩"
INVISIBLE = ZERO_WIDTH + BIDI_CONTROLS
_INVISIBLE_RE = re.compile(f"[{re.escape(INVISIBLE)}]")

# Glyphs that all mean "list item". A résumé exported from Word, Pages and
# LaTeX will use three different ones; downstream section and chunk logic should
# not have to know that.
_BULLETS = "•●▪▫◦‣⁃∙·–—*"
_BULLET_LINE_RE = re.compile(rf"^[ \t]*[{re.escape(_BULLETS)}][ \t]+", re.MULTILINE)

# "Kuber-\nnetes" -> "Kubernetes". Only when the fragment before the break is
# lowercase and the continuation is lowercase: "Full-\nStack" is a real hyphen
# at a line break, and "AWS-\nnative" would be mangled by a greedy rule.
_LINE_WRAP_HYPHEN_RE = re.compile(r"([a-z])-\n([a-z])")

_TRAILING_SPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Normalized:
    """The canonical text plus what had to be removed to get there."""

    text: str
    invisible_chars_removed: int
    control_chars_removed: int


def normalize(raw: str) -> Normalized:
    """Produce the canonical form of extracted document text.

    Order matters and is not arbitrary:

    1. NFKC first, so ligatures ("ﬁ" -> "fi") and full-width characters become
       their ASCII equivalents *before* anything pattern-matches on them.
    2. Newlines unified, so the hyphen and blank-line rules only handle "\\n".
    3. Invisibles stripped, so a zero-width space cannot hide inside a keyword
       and defeat the lexical index ("Kuber\\u200bnetes" is not "Kubernetes").
    4. De-hyphenate, which needs real newlines and clean characters to be right.
    5. Bullets and whitespace last — pure presentation.
    """
    text = unicodedata.normalize("NFKC", raw)

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    text, invisible_removed = _INVISIBLE_RE.subn("", text)

    text, control_removed = _strip_control_characters(text)

    text = _LINE_WRAP_HYPHEN_RE.sub(r"\1\2", text)

    text = _BULLET_LINE_RE.sub("- ", text)

    text = _TRAILING_SPACE_RE.sub("", text)
    text = _EXCESS_BLANK_LINES_RE.sub("\n\n", text)

    return Normalized(
        text=text.strip(),
        invisible_chars_removed=invisible_removed,
        control_chars_removed=control_removed,
    )


def _strip_control_characters(text: str) -> tuple[str, int]:
    """Remove C0/C1 controls, keeping tab and newline.

    PDF extraction routinely emits stray form feeds and vertical tabs. They
    render as nothing, count as characters, and would otherwise shift every
    offset after them for no reader-visible reason.
    """
    kept: list[str] = []
    removed = 0
    for char in text:
        if char in "\n\t":
            kept.append(char)
        elif unicodedata.category(char) == "Cc":
            removed += 1
        else:
            kept.append(char)
    return "".join(kept), removed
