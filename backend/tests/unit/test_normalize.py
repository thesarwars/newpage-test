"""The canonical-text contract.

Every citation offset in this system indexes into `normalize()`'s output. If it
is not idempotent, a re-ingest moves every stored offset and the evidence panel
highlights the wrong span — with no error anywhere. Hence the property tests:
handwritten examples do not find the counterexamples that matter here.
"""

from __future__ import annotations

import unicodedata

from hypothesis import given
from hypothesis import strategies as st

from apps.documents.normalize import INVISIBLE, normalize

# Deliberately hostile alphabet: invisibles, bidi controls, control characters,
# ligatures, bullets, hyphens and newlines — the things that actually appear in
# text extracted from a PDF.
_HOSTILE = st.text(
    alphabet=st.sampled_from(
        list("abcXYZ 	\n\r-–—•*·:;.")
        + list(INVISIBLE)
        + list("ﬁﬂ")  # ligatures NFKC should expand
        + ["\x00", "\x0b", "\x0c", "\x1f"]
        + list("ＡＢ")  # full-width, NFKC should fold
    ),
    max_size=300,
)


@given(_HOSTILE)
def test_normalize_is_idempotent(raw: str) -> None:
    """normalize(normalize(x)) == normalize(x).

    The load-bearing property. Offsets are computed once against this output and
    stored; if a second pass would change the string, those offsets are only
    valid until something re-normalizes.
    """
    once = normalize(raw).text

    assert normalize(once).text == once


@given(_HOSTILE)
def test_normalize_never_emits_invisible_or_control_characters(raw: str) -> None:
    """Invisible characters are the whole point of the exercise.

    A zero-width space inside "Kuber\\u200bnetes" defeats the lexical index while
    looking identical to a human — both as a retrieval bug and as an injection
    vector.
    """
    text = normalize(raw).text

    assert not any(char in INVISIBLE for char in text)
    assert not any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in text)


@given(_HOSTILE)
def test_normalize_reports_what_it_removed(raw: str) -> None:
    """Removal must be countable, so sanitize.scan() can flag a hidden payload.

    Stripping invisibles silently would destroy the evidence that they were
    there — which is exactly what an attacker would want.
    """
    expected = sum(1 for char in unicodedata.normalize("NFKC", raw) if char in INVISIBLE)

    assert normalize(raw).invisible_chars_removed == expected


def test_line_wrapped_hyphens_are_rejoined() -> None:
    assert normalize("We use Kuber-\nnetes daily").text == "We use Kubernetes daily"


def test_real_hyphenation_survives() -> None:
    """A greedy de-hyphenation rule would mangle these.

    "Full-\\nStack" is a genuine hyphenated compound broken across lines, not a
    word split by the typesetter.
    """
    assert "Full-\nStack" in normalize("Full-\nStack Engineer").text
    assert "AWS-\nnative" in normalize("AWS-\nnative").text


def test_bullet_glyphs_are_unified() -> None:
    text = normalize("• First\n▪ Second\n‣ Third\n* Fourth").text

    assert text.splitlines() == ["- First", "- Second", "- Third", "- Fourth"]


def test_ligatures_and_fullwidth_fold_to_ascii() -> None:
    assert normalize("ﬁle").text == "file"
    assert normalize("ＡＢ").text == "AB"


def test_excess_blank_lines_collapse_but_paragraphs_survive() -> None:
    assert normalize("A\n\n\n\n\nB").text == "A\n\nB"
    assert normalize("A\n\nB").text == "A\n\nB"


def test_crlf_is_unified_before_offsets_are_taken() -> None:
    """A Windows-exported .txt must not produce different offsets to a Unix one."""
    assert normalize("A\r\nB").text == normalize("A\nB").text


def test_empty_and_whitespace_only_input() -> None:
    assert normalize("").text == ""
    assert normalize("   \n\n \t ").text == ""
