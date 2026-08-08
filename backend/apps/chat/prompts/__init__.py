"""The frozen system prompt.

Read once at import and never interpolated. Two reasons, and the second is the
one that bites:

1. A prompt that varies per request is a prompt you cannot reason about. Every
   answer in the ledger is attributable to a `PROMPT_VERSION`, and that is only
   true if the bytes are fixed.
2. Prompt caching is a *prefix* match. One interpolated timestamp, session id or
   document count in the system block invalidates the cache on every single
   request, at 10x the input cost — and nothing errors. The failure is silent
   and shows up as a bill. `tests/unit/test_prompt.py` pins the SHA so a change
   is deliberate rather than incidental.

The split into a shared body and a mode-specific suffix is a caching decision:
the shared body carries `cache_control` and is byte-identical across analysis and
interview mode, so switching modes mid-session reuses the cached prefix instead
of paying to re-ingest ~900 tokens. The mode line is a second, uncached block.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def _load(name: str) -> str:
    # Trailing newline stripped so an editor that adds or removes one does not
    # change the SHA and silently invalidate every cached prefix.
    return (_DIR / name).read_text(encoding="utf-8").strip()


SYSTEM_PROMPT = _load("system.md")
ROLE_PROMPTS = {
    "analysis": _load("role_analysis.md"),
    "interview": _load("role_interview.md"),
}

# Derived, not hand-maintained: a version constant someone forgets to bump is
# worse than none, because it asserts something false.
PROMPT_VERSION = "v1." + hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:8]


def role_prompt(mode: str) -> str:
    return ROLE_PROMPTS.get(mode, ROLE_PROMPTS["analysis"])
