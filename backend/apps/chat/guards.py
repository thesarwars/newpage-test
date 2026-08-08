"""Deterministic refusals, decided before a single token is spent.

Two categories are caught here rather than delegated to the model:

**Out of scope.** "What's the weather" does not need an LLM to decline. The
router already classified it; this module writes the copy.

**Fabrication requests.** "Write me three years of Kubernetes I don't have" is
the request this product must get right, and getting it right means refusing
*and then being useful* — a career tool that helps someone lie is a liability to
the person using it, and a refusal with no alternative is merely useless. So the
refusal carries a redirect the UI renders as a one-click follow-up.

Doing it deterministically also means the guarantee holds with no API key, which
is the state a reviewer will run this in.

This is a regex, and a regex misses paraphrases. It is the *first* layer, not
the only one: the system prompt refuses fabrication independently, and the
golden set probes both. Stated plainly because a guardrail whose limits are
undocumented gets trusted past them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from apps.chat.models import RefusalReason
from apps.rag.router import Intent

# "make my résumé say", "add 3 years of", "pretend I", "claim that I led"…
# Anchored on the *ask to alter the record*, not on any mention of experience,
# so "how do I frame my Kubernetes experience?" is not caught by it.
_FABRICATION = re.compile(
    r"\b("
    r"(make|have|let)\s+(my\s+)?(r[ée]sum[ée]|cv|it)\s+(say|claim|show|state)"
    r"|(write|add|invent|fabricate|make\s+up|create)\s+(me\s+)?"
    r"(some\s+|a\s+few\s+|\d+\s+)?(years?|months?|experience|a\s+job|a\s+role|a\s+title)"
    r"|pretend\s+(that\s+)?i\s+"
    r"|(claim|say)\s+(that\s+)?i\s+(led|managed|built|have|worked)"
    r"|lie\s+(about|on)"
    r"|exaggerate"
    r"|backdate"
    r"|hide\s+(the\s+)?(gap|my\s+age|my\s+graduation)"
    r")",
    re.I,
)


@dataclass(frozen=True)
class Refusal:
    reason: str
    message: str
    suggestion: str = ""


_OUT_OF_SCOPE = Refusal(
    reason=RefusalReason.OUT_OF_SCOPE,
    message=(
        "That's outside what I can help with — I only analyze your résumé "
        "against the job descriptions you've uploaded."
    ),
    suggestion="Ask about your fit for a specific role, or what a posting requires that you're missing.",
)

_FABRICATION_REFUSAL = Refusal(
    reason=RefusalReason.FABRICATION_REQUEST,
    message=(
        "I won't write experience you don't have. Beyond the ethics, it's a bad "
        "bet: the claim has to survive a technical interview, and an interviewer "
        "who finds the gap stops trusting the rest of the résumé too."
    ),
    suggestion="Show me how to frame the experience I do have.",
)


def check(message: str, *, intent: Intent) -> Refusal | None:
    """The refusal for this message, or None to proceed.

    Fabrication is checked before intent because the router will happily
    classify "add 3 years of Kubernetes to my résumé" as an alignment question —
    it contains none of the out-of-scope vocabulary and every bit of the
    career vocabulary.
    """
    if _FABRICATION.search(message):
        return _FABRICATION_REFUSAL
    if intent == Intent.OUT_OF_SCOPE:
        return _OUT_OF_SCOPE
    return None
