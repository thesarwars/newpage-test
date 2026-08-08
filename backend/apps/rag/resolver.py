"""Scope resolution: which job is "Job #2"?

The assignment's own example query is *"How does my experience align with Job
#2?"*. Accuracy on that should be 100% and free — not a 250ms classifier call on
the critical path of every turn, with a small but non-zero chance of picking the
wrong document.

So this is a rule table, and it handles the phrasings people actually use:
ordinals, positional words, company names, and "this role" meaning whatever the
UI's scope pill is set to. An LLM classifier is the documented fallback for
genuine ambiguity (M5), not the first resort.

Returns the resolved document ids *and* what resolved them, so the UI can snap
its scope pill mid-stream and the user can see the system understood them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ORDINAL_WORDS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
    "last": -1,
}

# "job #2", "job 2", "role 2", "position #3", "#2"
_NUMBERED = re.compile(
    r"\b(?:job|role|position|posting|opening)?\s*#\s*(\d+)\b|\b(?:job|role|position|posting|opening)\s+(\d+)\b",
    re.I,
)
_ORDINAL = re.compile(
    r"\b(first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)\s+(?:job|role|position|posting|one)\b",
    re.I,
)
_ALL = re.compile(
    r"\b(all|every|each|both)\b.{0,20}\b(job|role|position|posting|them)\b|\bacross\b", re.I
)
_CURRENT = re.compile(r"\bthis (job|role|position|posting|one)\b|\bit\b$", re.I)


@dataclass(frozen=True)
class JobRef:
    """The minimum needed to resolve a reference."""

    document_id: str
    ordinal: int
    label: str
    company: str = ""


@dataclass(frozen=True)
class Scope:
    document_ids: list[str]
    resolved_from: str
    ambiguous: bool = False


def resolve(message: str, *, jobs: list[JobRef], explicit_ids: list[str] | None = None) -> Scope:
    """Work out which jobs a message is about.

    `explicit_ids` comes from the UI's scope pill and wins outright — if the user
    set a control, second-guessing it from their prose is a worse product.
    """
    if explicit_ids:
        return Scope(document_ids=list(explicit_ids), resolved_from="scope selection")

    if not jobs:
        return Scope(document_ids=[], resolved_from="no job descriptions uploaded")

    all_ids = [job.document_id for job in jobs]

    if _ALL.search(message):
        return Scope(document_ids=all_ids, resolved_from="all jobs")

    by_ordinal = {job.ordinal: job for job in jobs}

    if match := _NUMBERED.search(message):
        number = int(match.group(1) or match.group(2))
        if job := by_ordinal.get(number):
            return Scope([job.document_id], resolved_from=match.group(0).strip())

    if match := _ORDINAL.search(message):
        index = _ORDINAL_WORDS[match.group(1).lower()]
        ordered = sorted(jobs, key=lambda j: j.ordinal)
        if index == -1:
            return Scope([ordered[-1].document_id], resolved_from=match.group(0).strip())
        if 0 < index <= len(ordered):
            return Scope([ordered[index - 1].document_id], resolved_from=match.group(0).strip())

    if named := _match_by_name(message, jobs):
        return Scope([named.document_id], resolved_from=named.company or named.label)

    if _CURRENT.search(message) and len(jobs) == 1:
        return Scope([jobs[0].document_id], resolved_from=jobs[0].label)

    # No reference at all. Searching every job is the right default: the user
    # asked a general question, and narrowing it to a guess would answer a
    # question they did not ask.
    return Scope(
        document_ids=all_ids,
        resolved_from="all jobs" if len(jobs) > 1 else jobs[0].label,
        ambiguous=len(jobs) > 1 and bool(_CURRENT.search(message)),
    )


def _match_by_name(message: str, jobs: list[JobRef]) -> JobRef | None:
    """Match a company or label mentioned in the message.

    Whole-token and case-insensitive. Requires 4+ characters so a company called
    "Go" or "AI" does not match every message that happens to contain the word.
    """
    lowered = message.lower()
    for job in jobs:
        for name in (job.company, job.label):
            token = name.strip().lower()
            if len(token) >= 4 and re.search(rf"(?<!\w){re.escape(token)}(?!\w)", lowered):
                return job
    return None
