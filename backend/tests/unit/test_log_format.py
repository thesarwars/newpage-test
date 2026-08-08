"""The production log renderer.

Logs are the observability story for this app (docs/PLAN.md §10), and a log
aggregator can only query them if they are actually machine-readable. These
tests assert the JSON path emits parseable JSON *and* that redaction survives
rendering — a redaction processor that runs after serialisation would be
useless, and nothing else would notice.
"""

from __future__ import annotations

import json
import logging

import pytest
import structlog

from config.logging import CONSOLE, JSON, configure_structlog


def test_unknown_format_is_rejected_loudly() -> None:
    """A typo in LOG_FORMAT should fail at startup, not silently pick a default."""
    with pytest.raises(ValueError, match="LOG_FORMAT must be"):
        configure_structlog(fmt="jsonn")


def test_both_supported_formats_build_a_usable_logging_dict() -> None:
    for fmt in (CONSOLE, JSON):
        config = configure_structlog(fmt=fmt)

        assert config["handlers"]["console"]["formatter"] == "structlog"
        assert config["root"]["level"] == "INFO"


def test_json_renderer_emits_parseable_lines_with_pii_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_structlog(fmt=JSON)
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
    )

    with caplog.at_level(logging.INFO):
        structlog.get_logger("t").info(
            "upload_received",
            detail="candidate jane.doe@example.com on +1 (415) 555-0142",
            document_id="doc-1",
        )

    payload = json.loads(formatter.format(caplog.records[0]))

    assert payload["event"] == "upload_received"
    assert payload["document_id"] == "doc-1"
    assert "jane.doe@example.com" not in payload["detail"]
    assert "555-0142" not in payload["detail"]
