"""The keyless backend. One class, two modes.

`mode="replay"` plays a recorded fixture back through the identical event
stream, which is how refusals, truncation and citation mapping are tested
without a network or a key.

`mode="stub"` is the *product* path when no key is configured. It is not a
placeholder string: the answer is assembled from the chunks retrieval actually
selected, and every citation carries a real offset into the real document, so
clicking `[1]` highlights the genuine span. A reviewer with no key still sees
retrieval, source chips, the evidence panel and the trace working on their own
uploads — everything except generated prose.

Why one class and not two: the stub and the replay must not be able to drift
apart in how they emit events, because the SSE layer's correctness depends on
both producing exactly what the real gateway produces.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from apps.observability import ledger
from apps.observability.models import CallPurpose
from llm.types import (
    ChatRequest,
    CitationDelta,
    Completed,
    DocumentBlock,
    StreamEvent,
    TextDelta,
    Usage,
)

# How many retrieved passages the stub quotes. Three is enough to show grouping
# across documents without turning the demo answer into a wall of quotes.
STUB_MAX_QUOTES = 3
STUB_MIN_LINE_CHARS = 25
STUB_MAX_QUOTE_CHARS = 180

# Emitted between deltas so the stub streams visibly rather than arriving whole.
# Small enough to look live, large enough not to flood the SSE channel.
STUB_DELTA_DELAY_S = 0.012

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class FakeAnthropic:
    """Backend-compatible stand-in for `AnthropicGateway`."""

    backend_name = "fake"

    def __init__(
        self,
        *,
        mode: Literal["replay", "stub"] = "stub",
        fixture: Path | str | None = None,
        delay_s: float = STUB_DELTA_DELAY_S,
    ) -> None:
        if mode == "replay" and fixture is None:
            raise ValueError("replay mode needs a fixture path")
        self.mode = mode
        self.fixture = Path(fixture) if fixture else None
        self.delay_s = delay_s
        self._chars_emitted = 0

    def stream(
        self,
        request: ChatRequest,
        *,
        session_id: Any = None,
        message_id: Any = None,
        purpose: str = CallPurpose.CHAT,
    ) -> Iterator[StreamEvent]:
        started = time.monotonic()
        ttft_ms: int | None = None
        stop_reason = "end_turn"

        try:
            events = self._replay_events() if self.mode == "replay" else self._stub_events(request)
            for event in events:
                if isinstance(event, TextDelta) and ttft_ms is None:
                    ttft_ms = int((time.monotonic() - started) * 1000)
                if isinstance(event, Completed):
                    stop_reason = event.stop_reason
                yield event
        finally:
            # The fake writes a ledger row too. It costs nothing, but the usage
            # panel, the trace drawer and `/usage/` all read this table — and a
            # keyless demo where those panels are empty would look broken rather
            # than free.
            ledger.record(
                session_id=session_id,
                message_id=message_id,
                purpose=purpose,
                model="fake",
                effort=request.effort,
                backend=self.backend_name,
                latency_ms=int((time.monotonic() - started) * 1000),
                ttft_ms=ttft_ms,
                stop_reason=stop_reason,
            )

    # ── replay ────────────────────────────────────────────────────────────────

    def _replay_events(self) -> Iterator[StreamEvent]:
        assert self.fixture is not None
        payload = json.loads(self.fixture.read_text(encoding="utf-8"))

        answer_chars = 0
        for raw in payload.get("events", []):
            kind = raw.get("type")
            if kind == "text_delta":
                text = raw.get("text", "")
                yield TextDelta(text=text)
                answer_chars += len(text)
            elif kind == "citation":
                yield CitationDelta(
                    document_index=int(raw["document_index"]),
                    start_char_index=int(raw["start_char_index"]),
                    end_char_index=int(raw["end_char_index"]),
                    cited_text=raw.get("cited_text", ""),
                    # Recomputed from the replayed deltas rather than read from
                    # the fixture, so a hand-edited fixture cannot encode an
                    # `answer_char` the text does not support.
                    answer_char=answer_chars,
                )

        usage = payload.get("usage", {})
        yield Completed(
            stop_reason=payload.get("stop_reason", "end_turn"),
            refusal_message=payload.get("refusal_message", ""),
            usage=Usage(
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                cache_read_tokens=int(usage.get("cache_read_input_tokens", 0)),
                cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0)),
            ),
            request_id=payload.get("request_id", ""),
        )

    # ── stub ──────────────────────────────────────────────────────────────────

    def _stub_events(self, request: ChatRequest) -> Iterator[StreamEvent]:
        self._chars_emitted = 0
        quotes = _pick_quotes(request.blocks)

        yield from self._emit(
            "**Demo answer.** No `ANTHROPIC_API_KEY` is configured, so this text is "
            "assembled from the passages retrieval selected — it is not model output. "
            "The citations below are real.\n\n"
        )

        if not quotes:
            yield from self._emit(
                "Retrieval returned no passages for that question, so there is nothing "
                "to quote. Try naming a specific job, or ask about a skill that appears "
                "in your documents."
            )
            yield Completed(stop_reason="end_turn")
            return

        yield from self._emit(f"Your question — *{request.question.strip()}* — retrieved ")
        yield from self._emit(f"{len(quotes)} passage{'s' if len(quotes) != 1 else ''}:\n\n")

        answer_chars = self._chars_emitted

        for position, (block_index, block, start, end) in enumerate(quotes, start=1):
            yield from self._emit(f"{position}. **{block.title}** — ")
            answer_chars = self._chars_emitted

            quoted = block.text[start:end]
            yield from self._emit(f"“{quoted}”")

            # The citation lands at the position where the quote *began*, which
            # is where the client splices the `[n]` mark. Offsets are shifted
            # into document coordinates by apps/chat/citations.py, using the same
            # base_offset arithmetic the real path uses.
            yield CitationDelta(
                document_index=block_index,
                start_char_index=start,
                end_char_index=end,
                cited_text=quoted,
                answer_char=answer_chars,
            )
            yield from self._emit("\n\n")

        yield from self._emit(
            "With a key set, this would be a generated analysis of those passages "
            "instead of a list of them. Everything else on this screen — retrieval, "
            "ranking, the source chips, the evidence highlighting — is already real."
        )
        yield Completed(stop_reason="end_turn")

    def _emit(self, text: str) -> Iterator[StreamEvent]:
        """Break text into word-sized deltas so the stub streams like the real thing."""
        for piece in re.findall(r"\S+\s*|\s+", text):
            self._chars_emitted += len(piece)
            yield TextDelta(text=piece)
            if self.delay_s:
                time.sleep(self.delay_s)


def _pick_quotes(blocks: list[DocumentBlock]) -> list[tuple[int, DocumentBlock, int, int]]:
    """One salient span per block, as exact offsets into that block's text.

    The block's own index travels with it — `document_index` must be the block's
    position in the request, and recovering it later with `list.index()` would
    return the *first* equal block, which is wrong the moment two chunks happen
    to carry identical text.

    Offsets are computed by slicing, never by re-searching for a string that was
    trimmed first — a `.strip()` here would shift every citation by however much
    whitespace it removed, which is precisely the class of bug the offset
    contract exists to make impossible.
    """
    picked: list[tuple[int, DocumentBlock, int, int]] = []
    for index, block in enumerate(blocks):
        if len(picked) >= STUB_MAX_QUOTES:
            break
        span = _salient_span(block.text)
        if span is not None:
            picked.append((index, block, span[0], span[1]))
    return picked


def _salient_span(text: str) -> tuple[int, int] | None:
    """The longest substantive line, trimmed to one sentence at most."""
    if not text.strip():
        return None

    best: tuple[int, int] | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if len(stripped) >= STUB_MIN_LINE_CHARS:
            start = offset + (len(line) - len(line.lstrip()))
            end = start + len(stripped)
            if best is None or (end - start) > (best[1] - best[0]):
                best = (start, end)
        offset += len(line)

    if best is None:
        end = min(len(text.rstrip()), STUB_MAX_QUOTE_CHARS)
        return (0, end) if end else None

    start, end = best
    sentence = _SENTENCE_SPLIT.split(text[start:end])[0]
    end = start + min(len(sentence), STUB_MAX_QUOTE_CHARS)
    # Never cut mid-word: back up to the last space if the cap bit into one.
    if end < start + len(sentence) and " " in text[start:end]:
        end = start + text[start:end].rindex(" ")
    return (start, end)
