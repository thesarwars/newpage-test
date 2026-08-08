"""The Anthropic request body, and the one parser whose contract is unverified.

Two things are worth testing here without a key:

1. **The request body.** Every parameter that returns 400 on opus-5 must be
   absent, and the cache breakpoints must land where they were designed to. A
   400 is cheap to discover in production; a silently-missing `cache_control` is
   not — it costs 10x on input and reports nothing.
2. **`_parse_event`.** It is the boundary between the SDK's event shape and
   ours. The fixture it runs against is written from the documented shape rather
   than a recorded response (see tests/fixtures/anthropic/README.md), so this
   test pins an assumption rather than a measurement — deliberately, and said
   out loud, so that `make smoke-live` turns a wrong assumption into a red test
   instead of a wrong highlight in the UI.
"""

from __future__ import annotations

import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from apps.observability.models import LLMCall
from llm import budget
from llm.gateway import AnthropicGateway, UpstreamError, _parse_event, _usage_from
from llm.types import ChatRequest, CitationDelta, Completed, DocumentBlock, TextDelta

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anthropic"


def _objectify(value: Any) -> Any:
    """JSON → attribute access, because the SDK hands back objects, not dicts."""
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _objectify(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_objectify(v) for v in value]
    return value


@pytest.fixture
def request_body() -> dict[str, Any]:
    gateway = AnthropicGateway("sk-test-not-a-real-key", model="claude-opus-5")
    chat = ChatRequest(
        system="SYSTEM BODY",
        system_suffix="MODE LINE",
        blocks=[
            DocumentBlock(title="Résumé", text="resume text", document_id="d1", cache=True),
            DocumentBlock(
                title="Job #1", text="chunk text", document_id="d2", base_offset=40, chunk_id="c1"
            ),
        ],
        question="What am I missing?",
        history=[("user", "earlier question"), ("assistant", "earlier answer")],
    )
    return gateway._payload(chat)


def test_no_parameter_that_returns_400(request_body: dict[str, Any]) -> None:
    """Each of these is a 400 on opus-5, not a silently-ignored field."""
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in request_body
    assert "budget_tokens" not in request_body["thinking"]
    assert request_body["thinking"] == {"type": "adaptive"}
    # An assistant turn *last* in the list is prefill, which is also a 400.
    assert request_body["messages"][-1]["role"] == "user"


def test_thinking_and_text_share_the_cap(request_body: dict[str, Any]) -> None:
    assert request_body["max_tokens"] == 8192


def test_cache_breakpoints(request_body: dict[str, Any]) -> None:
    """Exactly two: the frozen system body, and the whole résumé.

    The mode line is deliberately *not* cached — keeping it out of the cached
    block is what lets analysis and interview mode share one cached prefix.
    """
    system = request_body["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system[1]
    assert system[1]["text"] == "MODE LINE"

    resume_block = request_body["messages"][0]["content"][0]
    assert resume_block["cache_control"] == {"type": "ephemeral"}

    tail = request_body["messages"][-1]["content"]
    assert "cache_control" not in tail[0], "retrieved chunks change every turn; caching them churns"


def test_document_blocks_carry_citations(request_body: dict[str, Any]) -> None:
    resume_block = request_body["messages"][0]["content"][0]
    assert resume_block["citations"] == {"enabled": True}
    assert resume_block["source"]["type"] == "text"
    # Verbatim. A block whose text differs from the document slice by even one
    # stripped newline shifts every citation offset in it.
    assert resume_block["source"]["data"] == "resume text"


def test_question_comes_last_in_the_final_turn(request_body: dict[str, Any]) -> None:
    """Volatile content last, so the cached prefix is as long as possible."""
    tail = request_body["messages"][-1]["content"]
    assert tail[-1] == {"type": "text", "text": "What am I missing?"}
    assert tail[0]["type"] == "document"


def test_server_side_fallback_is_on(request_body: dict[str, Any]) -> None:
    assert request_body["betas"] == ["server-side-fallback-2026-07-01"]
    assert request_body["fallbacks"] == "default"


def test_gateway_refuses_to_construct_without_a_key() -> None:
    with pytest.raises(ValueError, match="FakeAnthropic"):
        AnthropicGateway("")


# ── the unverified boundary ──────────────────────────────────────────────────


def test_parses_the_documented_stream_shape() -> None:
    fixture = json.loads((FIXTURES / "raw_stream_events.json").read_text())
    parsed = []
    answer_chars = 0
    for raw in fixture["events"]:
        event = _parse_event(_objectify(raw), answer_chars=answer_chars)
        if event is None:
            continue
        parsed.append(event)
        if isinstance(event, TextDelta):
            answer_chars += len(event.text)

    texts = [e for e in parsed if isinstance(e, TextDelta)]
    citations = [e for e in parsed if isinstance(e, CitationDelta)]

    assert len(texts) == 2
    assert len(citations) == 1

    citation = citations[0]
    assert citation.document_index == 1
    assert citation.start_char_index == 12
    assert citation.end_char_index == 52
    assert citation.cited_text == "5+ years running Kubernetes in production"
    # Recorded at the position the citation arrived — i.e. after the first text
    # delta and before the second. Only knowable mid-stream.
    assert citation.answer_char == len(texts[0].text)


def test_unknown_events_are_skipped_not_fatal() -> None:
    """An event shape we don't recognise costs a citation, never the answer."""
    assert _parse_event(_objectify({"type": "message_start"}), answer_chars=0) is None
    assert (
        _parse_event(
            _objectify({"type": "content_block_delta", "delta": {"type": "thinking_delta"}}),
            answer_chars=0,
        )
        is None
    )
    # A citation type other than char_location (e.g. page_location from a PDF
    # source) has no offset we can map, so it is dropped rather than guessed at.
    assert (
        _parse_event(
            _objectify(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "citations_delta", "citation": {"type": "page_location"}},
                }
            ),
            answer_chars=0,
        )
        is None
    )


def test_usage_survives_a_missing_field() -> None:
    usage = _usage_from(_objectify({"input_tokens": 10, "output_tokens": 5}))
    assert usage.input_tokens == 10
    assert usage.cache_read_tokens == 0
    assert _usage_from(None).input_tokens == 0


# ── the ledger, against the real gateway ─────────────────────────────────────


class _FakeStream:
    """Stands in for the SDK's stream context manager."""

    def __init__(self, events: list[Any], final: Any, delay_s: float = 0.0) -> None:
        self._events = events
        self._final = final
        self._delay_s = delay_s

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def __iter__(self) -> Any:
        for event in self._events:
            if self._delay_s:
                time.sleep(self._delay_s)
            yield event

    def get_final_message(self) -> Any:
        return self._final


def _install_fake_sdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[Any] | None = None,
    final: Any = None,
    raises: Exception | None = None,
    delay_s: float = 0.0,
) -> None:
    """Inject a module named `anthropic`, since the gateway imports it lazily."""

    def make_stream(**payload: Any) -> Any:
        if raises is not None:
            raise raises
        return _FakeStream(events or [], final, delay_s=delay_s)

    module = SimpleNamespace(
        Anthropic=lambda **_: SimpleNamespace(
            beta=SimpleNamespace(messages=SimpleNamespace(stream=make_stream))
        ),
        Timeout=lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "anthropic", module)


def _stream_fixture_events() -> list[Any]:
    fixture = json.loads((FIXTURES / "raw_stream_events.json").read_text())
    return [_objectify(raw) for raw in fixture["events"]]


@pytest.mark.django_db
class TestLedger:
    def test_successful_call_is_recorded_with_its_cost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_sdk(
            monkeypatch,
            events=_stream_fixture_events(),
            final=_objectify(
                {
                    "stop_reason": "end_turn",
                    "stop_details": None,
                    "usage": {
                        "input_tokens": 2841,
                        "output_tokens": 96,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 1104,
                    },
                }
            ),
        )
        gateway = AnthropicGateway("sk-test", model="claude-opus-5")

        events = [e for e in gateway.stream(_chat_request()) if e is not None]

        completed = events[-1]
        assert isinstance(completed, Completed)
        assert completed.stop_reason == "end_turn"

        row = LLMCall.objects.get()
        assert row.input_tokens == 2841
        assert row.output_tokens == 96
        assert row.cache_creation_tokens == 1104
        # (2841*5) + (1104*5*1.25) + (96*25) = 14205 + 6900 + 2400 millionths
        # of a dollar. The 1.25x cache-*write* multiplier is the one people forget: a cold
        # first turn costs more than an uncached one, and only pays back on turn
        # two. It is in the arithmetic here so it stays in the arithmetic there.
        assert row.cost_usd == Decimal("0.023505")
        assert row.ttft_ms is not None, "time-to-first-token is the metric the UX is built on"

    def test_failed_call_still_leaves_a_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The `finally` ledger. Without it, the calls you most want to see vanish."""
        _install_fake_sdk(monkeypatch, raises=TimeoutError("upstream stalled"))
        gateway = AnthropicGateway("sk-test")

        with pytest.raises(UpstreamError):
            list(gateway.stream(_chat_request()))

        row = LLMCall.objects.get()
        assert row.error_type == "TimeoutError"
        assert row.cost_usd == Decimal("0")

    def test_budget_refusal_records_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A call the budget refused to make is not a call."""
        _install_fake_sdk(monkeypatch, events=[], final=None)
        monkeypatch.setattr(budget, "check", _raise_budget)
        gateway = AnthropicGateway("sk-test")

        with pytest.raises(budget.BudgetExhaustedError):
            list(gateway.stream(_chat_request()))

        assert LLMCall.objects.count() == 0

    def test_silence_produces_heartbeats(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Adaptive thinking can hold the connection quiet for tens of seconds.

        Only this layer can notice — a generator blocked in `next()` cannot —
        which is why the worker thread lives in the gateway.
        """
        _install_fake_sdk(
            monkeypatch,
            events=_stream_fixture_events(),
            final=_objectify({"stop_reason": "end_turn", "stop_details": None, "usage": {}}),
            delay_s=0.05,
        )
        monkeypatch.setattr("llm.gateway.HEARTBEAT_S", 0.01)
        gateway = AnthropicGateway("sk-test")

        emitted = list(gateway.stream(_chat_request()))

        assert None in emitted, "no heartbeat during a silent upstream"
        assert any(isinstance(e, TextDelta) for e in emitted)

    def test_refusal_survives_stop_details_being_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`stop_reason == "refusal"` does not imply `stop_details` is populated.

        Assuming it does is an AttributeError raised while handling the one
        response shape you cannot re-request.
        """
        _install_fake_sdk(
            monkeypatch,
            events=[],
            final=_objectify({"stop_reason": "refusal", "stop_details": None, "usage": {}}),
        )
        gateway = AnthropicGateway("sk-test")

        events = [e for e in gateway.stream(_chat_request()) if e is not None]

        completed = events[-1]
        assert isinstance(completed, Completed)
        assert completed.stop_reason == "refusal"
        assert completed.refusal_message == ""
        assert LLMCall.objects.get().stop_reason == "refusal"


def _raise_budget() -> None:
    raise budget.BudgetExhaustedError(Decimal("10"), Decimal("10"))


def _chat_request() -> ChatRequest:
    return ChatRequest(
        system="SYSTEM",
        blocks=[DocumentBlock(title="Résumé", text="resume text", document_id="d1")],
        question="What am I missing?",
    )
