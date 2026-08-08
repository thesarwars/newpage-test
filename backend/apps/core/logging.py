"""Logging primitives: PII redaction and the safe-reference helper.

Deliberately free of Django imports so it can be wired from settings without an
import cycle, and unit-tested without a configured app registry.

A résumé is PII by construction. The rule this module enforces is that **document
text never reaches a log line** — logs carry ids and content hashes, and that is
the whole vocabulary. Two layers, because one is not enough:

1. `log_safe()` is the only sanctioned way to reference a document in a log. It
   physically cannot emit text, because it never receives it.
2. `redact_pii` is a structlog processor that scrubs emails, phone numbers and
   URLs from every event that reaches the chain anyway — the backstop for the
   log line somebody adds in a hurry at 2am.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

# Deliberately conservative. These run on every log event, so they are cheap
# patterns aimed at the three things a résumé reliably contains, not a general
# PII detector — that would be a false promise.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
_URL = re.compile(r"https?://\S+")

_REDACTED = "[redacted]"

# Keys whose values are structurally safe and must survive redaction, so a
# request id or a content hash is never mangled into uselessness.
_NEVER_REDACT = frozenset(
    {
        "event",
        "level",
        "logger",
        "timestamp",
        "request_id",
        "session_hash",
        "document_id",
        "chunk_id",
        "message_id",
        "text_sha256",
        "content_sha256",
        "status_code",
        "duration_ms",
        "error_code",
    }
)


def scrub(value: str) -> str:
    """Redact emails, phone numbers and URLs from a single string."""
    value = _EMAIL.sub(_REDACTED, value)
    value = _URL.sub(_REDACTED, value)
    return _PHONE.sub(_REDACTED, value)


def redact_pii(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: scrub PII from every string value in the event."""
    for key, value in event_dict.items():
        if key in _NEVER_REDACT:
            continue
        if isinstance(value, str):
            event_dict[key] = scrub(value)
    return event_dict


def session_hash(token: str, *, secret: str) -> str:
    """Stable, non-reversible handle for a session.

    Logs correlate on this rather than the raw session token: the token is a
    bearer credential, and a log aggregator is the last place it should live.
    """
    digest = hmac.new(secret.encode(), token.encode(), hashlib.sha256)
    return digest.hexdigest()[:16]


def log_safe(document: Any) -> dict[str, Any]:
    """Return the only representation of a document that may be logged.

    Never returns text, filename, or any field derived from document content
    other than a hash. `tests/unit/test_logging.py` asserts that property against
    a document whose every text field is a known sentinel.
    """
    return {
        "document_id": str(getattr(document, "id", "")),
        "kind": getattr(document, "kind", None),
        "ordinal": getattr(document, "ordinal", None),
        "text_sha256": getattr(document, "text_sha256", None),
        "page_count": getattr(document, "page_count", None),
        "status": getattr(document, "status", None),
    }
