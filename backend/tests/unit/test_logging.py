"""Redaction and the safe-reference helper.

The claim in the README is that logs never carry document text. These are the
tests that make it a claim rather than a hope.
"""

from __future__ import annotations

from apps.core.logging import log_safe, redact_pii, scrub, session_hash


class _FakeDocument:
    """Every text-bearing field is a sentinel, so a leak is unmistakable."""

    id = "11111111-1111-1111-1111-111111111111"
    kind = "resume"
    ordinal = 0
    text_sha256 = "abc123"
    page_count = 2
    status = "ready"

    # None of these may ever appear in log_safe() output.
    normalized_text = "LEAK_NORMALIZED_TEXT"
    text = "LEAK_TEXT"
    original_filename = "LEAK_FILENAME.pdf"
    label = "LEAK_LABEL"
    company = "LEAK_COMPANY"


def test_log_safe_emits_no_document_content() -> None:
    payload = log_safe(_FakeDocument())

    rendered = repr(payload)
    assert "LEAK" not in rendered, f"log_safe leaked document content: {rendered}"

    # And it is still useful — a hash and an id are enough to correlate.
    assert payload["document_id"] == _FakeDocument.id
    assert payload["text_sha256"] == "abc123"


def test_log_safe_never_grows_a_text_field_by_accident() -> None:
    """Pin the key set.

    Adding a field to log_safe should be a deliberate act with a test change
    attached, not something that rides along in an unrelated commit.
    """
    assert set(log_safe(_FakeDocument())) == {
        "document_id",
        "kind",
        "ordinal",
        "text_sha256",
        "page_count",
        "status",
    }


def test_scrub_removes_emails_phones_and_urls() -> None:
    dirty = (
        "reach me at jane.doe+cv@example.com or +1 (415) 555-0142, portfolio https://jane.dev/cv"
    )
    clean = scrub(dirty)

    assert "jane.doe+cv@example.com" not in clean
    assert "555-0142" not in clean
    assert "jane.dev" not in clean
    assert "reach me at" in clean  # non-PII text survives


def test_redact_processor_scrubs_string_values() -> None:
    event = {"event": "upload", "detail": "contact bob@example.com", "document_id": "abc"}

    result = redact_pii(None, "info", event)

    assert "bob@example.com" not in result["detail"]
    assert result["document_id"] == "abc"  # structural keys survive untouched
    assert result["event"] == "upload"


def test_redact_preserves_non_string_values() -> None:
    event = {"event": "usage", "tokens": 1234, "cost": 0.043, "ok": True}

    assert redact_pii(None, "info", event) == event


def test_session_hash_is_stable_and_not_the_token() -> None:
    token = "s3cret-session-token"

    digest = session_hash(token, secret="k")

    assert digest == session_hash(token, secret="k")
    assert token not in digest
    assert digest != session_hash(token, secret="different-key")
