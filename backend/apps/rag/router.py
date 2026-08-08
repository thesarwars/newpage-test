"""Intent routing.

A rule table, not a classifier. It runs in microseconds, costs nothing, is
deterministic in tests, and — the part that matters — it is *inspectable*: when
retrieval anchors the wrong section you can read why, instead of re-prompting a
model and hoping.

The intent selects the prompt template (M5), the retrieval `k`, and which
sections get force-anchored. Getting it wrong degrades an answer; it does not
break one, which is why a cheap deterministic rule is the right trade.
"""

from __future__ import annotations

import re
from enum import StrEnum

from apps.documents.chunking.sections import SectionKind


class Intent(StrEnum):
    GAP = "gap"
    FIT = "fit"
    ALIGNMENT = "alignment"
    INTERVIEW = "interview"
    COMPARE = "compare"
    META = "meta"
    OUT_OF_SCOPE = "out_of_scope"


# Ordered: first match wins, most specific first.
_RULES: list[tuple[Intent, re.Pattern[str]]] = [
    (
        Intent.OUT_OF_SCOPE,
        re.compile(
            r"\b(weather|recipe|joke|capital of|translate|who won|stock price|"
            r"write me a (poem|story)|what is your (system )?prompt)\b",
            re.I,
        ),
    ),
    (
        Intent.INTERVIEW,
        re.compile(
            r"\b(interview|prepare|preparation|questions? (they|i).{0,15}ask|star method|talking points?)\b",
            re.I,
        ),
    ),
    (
        Intent.COMPARE,
        re.compile(
            r"\b(compare|versus|vs\.?|which (job|role|one)|better fit|rank|between these)\b", re.I
        ),
    ),
    (
        Intent.GAP,
        re.compile(
            r"\b(missing|gap|lack|don'?t have|need to learn|weak(nesse)?s?|short of|not qualified|upskill)\b",
            re.I,
        ),
    ),
    (
        Intent.FIT,
        re.compile(r"\b(fit|qualified|suitable|good match|chances|should i apply|am i a)\b", re.I),
    ),
    (
        Intent.ALIGNMENT,
        re.compile(r"\b(align|match|relevant|map|how does my|which of my|transferable)\b", re.I),
    ),
    (
        Intent.META,
        re.compile(
            r"\b(what (documents|files|jobs)|what did i upload|how many|what can you)\b", re.I
        ),
    ),
]

# Sections force-anchored per intent. Reasoning about a gap without the
# requirements list is the single most embarrassing failure available here.
_ANCHORS: dict[Intent, tuple[SectionKind, ...]] = {
    Intent.GAP: (SectionKind.REQUIREMENTS, SectionKind.NICE_TO_HAVE),
    Intent.FIT: (SectionKind.REQUIREMENTS,),
    Intent.ALIGNMENT: (SectionKind.REQUIREMENTS,),
    Intent.COMPARE: (SectionKind.REQUIREMENTS,),
    Intent.INTERVIEW: (SectionKind.REQUIREMENTS, SectionKind.RESPONSIBILITIES),
}


def route(message: str) -> Intent:
    for intent, pattern in _RULES:
        if pattern.search(message):
            return intent
    # Unmatched questions are treated as alignment rather than out-of-scope:
    # refusing a question the rules simply did not anticipate is a much worse
    # failure than answering it with slightly wrong anchors.
    return Intent.ALIGNMENT


def anchor_sections(intent: Intent) -> tuple[SectionKind, ...]:
    return _ANCHORS.get(intent, ())


def expands_query(intent: Intent) -> bool:
    """Whether to widen the lexical query with the job's requirement skills.

    Only for gap and interview work: the user's phrasing ("what am I missing?")
    rarely contains the skill nouns, so without expansion the lexical arm has
    almost nothing to match on.
    """
    return intent in (Intent.GAP, Intent.INTERVIEW, Intent.FIT)
