"""The system prompt is frozen. These tests are what "frozen" means.

The SHA pin is not ceremony. Prompt caching is a *prefix* match: one edited word
in the system block invalidates the cache on every request in every session, at
full input price, and nothing errors. The failure is a bill, not a stack trace.
Pinning the hash turns an accidental edit into a red test and a deliberate one
into a line in the diff that says so.
"""

from __future__ import annotations

import re

from apps.chat.prompts import PROMPT_VERSION, ROLE_PROMPTS, SYSTEM_PROMPT, role_prompt
from apps.documents.chunking.tokenizer import count_tokens

# Update deliberately, in the same commit as the prompt change.
EXPECTED_PROMPT_VERSION = "v1.c655a9dc"


def test_prompt_version_is_pinned() -> None:
    assert PROMPT_VERSION == EXPECTED_PROMPT_VERSION, (
        "The system prompt changed. That is fine — but it invalidates every "
        "cached prefix and relabels the provenance of future answers, so update "
        "EXPECTED_PROMPT_VERSION in the same commit and say why in the message."
    )


def test_prompt_clears_the_cache_minimum() -> None:
    """opus-5 will not cache a block under 512 tokens — it silently declines.

    Counted with bge's tokenizer, which is not Claude's; it runs ~10-20% high on
    English prose, so the assertion uses a margin rather than the bare limit.
    """
    assert count_tokens(SYSTEM_PROMPT) > 700


def test_prompt_has_no_interpolation() -> None:
    """No f-string holes, no `.format` slots, no template tags.

    A prompt that can vary per request is a prompt that cannot be cached and
    cannot be attributed. This asserts the shape rather than trusting review.
    """
    assert not re.search(r"\{[a-z_]+\}", SYSTEM_PROMPT)
    assert "%s" not in SYSTEM_PROMPT
    assert "{{" not in SYSTEM_PROMPT


def test_prompt_states_the_four_contracts() -> None:
    """Grounding, injection, fabrication, bias — each has to actually be in there.

    Coarse, and deliberately so: it catches a section deleted in a refactor,
    which is the realistic failure. It does not pretend to measure whether the
    model obeys them — that is what the golden set is for.
    """
    lowered = SYSTEM_PROMPT.lower()
    assert "never invent" in lowered
    assert "instructions to follow" in lowered
    assert "fabricate experience" in lowered
    assert "protected attributes" in lowered


def test_modes_swap_only_the_role_section() -> None:
    analysis = role_prompt("analysis")
    interview = role_prompt("interview")
    assert analysis != interview
    # The cached body is shared by both, which is the entire reason the mode
    # line is a separate block.
    assert SYSTEM_PROMPT not in analysis
    assert SYSTEM_PROMPT not in interview


def test_unknown_mode_falls_back_to_analysis() -> None:
    assert role_prompt("nonsense") == ROLE_PROMPTS["analysis"]
