"""The only place this project calls Anthropic.

One call site is a design constraint, not tidiness. It is what makes the cost
ledger complete (every call is recorded because there is nowhere else to make
one), the daily ceiling unbypassable, and the model parameters auditable in a
single 40-line function instead of scattered across five features.

Every parameter below is verified against the opus-5 model facts. The ones that
are *absent* matter as much as the ones present: `temperature`, `top_p`,
`top_k`, `budget_tokens` and assistant-turn prefill each return 400 on this
model, so none of them appear anywhere in this file.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from queue import Empty, Queue
from threading import Thread
from typing import Any

import structlog
from django.conf import settings

from apps.observability import ledger
from apps.observability.models import CallPurpose
from llm import budget
from llm.pricing import cost_usd
from llm.types import (
    ChatRequest,
    CitationDelta,
    Completed,
    StreamEvent,
    TextDelta,
    Usage,
)

log = structlog.get_logger(__name__)

# Comment frames during silence. The dangerous gap is between the request going
# out and the first token: adaptive thinking can run for tens of seconds, and an
# idle connection is what proxies reap.
HEARTBEAT_S = 15.0

# Category-routed server-side retry. A benign résumé that trips a classifier is
# rescued inside the same call rather than dead-ending on the user.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Connect fast, then wait: a stalled TCP handshake should fail in seconds, but a
# long answer legitimately takes a while to finish streaming.
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 120.0


class UpstreamError(Exception):
    """Anthropic failed in a way the user needs told about."""

    def __init__(self, message: str, *, error_type: str, retry_after: int | None = None) -> None:
        self.error_type = error_type
        self.retry_after = retry_after
        super().__init__(message)


class AnthropicGateway:
    """Streams one assistant turn and writes exactly one ledger row."""

    backend_name = "anthropic"

    def __init__(self, api_key: str, *, model: str | None = None) -> None:
        if not api_key:
            raise ValueError("AnthropicGateway requires an API key; use FakeAnthropic instead.")
        self._api_key = api_key
        self._model = model or settings.CIA_CHAT_MODEL

    def stream(
        self,
        request: ChatRequest,
        *,
        session_id: Any = None,
        message_id: Any = None,
        purpose: str = CallPurpose.CHAT,
    ) -> Iterator[StreamEvent | None]:
        """Yields events, plus `None` whenever the upstream goes quiet.

        A `None` is the caller's cue to emit an SSE heartbeat. It is produced
        here rather than by the caller because only this layer knows when the
        network is idle — and adaptive thinking can hold the connection silent
        for tens of seconds, which is exactly how long a proxy waits before
        reaping it.
        """
        # Before the `try`, deliberately: a call the budget refused to make is
        # not a call, and a ledger row for it would inflate the call count and
        # the cache-hit denominator with an event that never reached the API.
        budget.check()

        started = time.monotonic()
        ttft_ms: int | None = None
        usage = Usage()
        stop_reason = ""
        error_type = ""
        request_id = ""

        try:
            # Imported here, not at module scope: with no key this module is
            # still imported (settings read it, tests introspect it), and the
            # keyless install should not require the SDK to be importable.
            import anthropic

            client = anthropic.Anthropic(
                api_key=self._api_key,
                timeout=anthropic.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
                max_retries=2,
            )

            final: Any = None
            for item in _drain(client, self._payload(request), heartbeat_s=HEARTBEAT_S):
                if item is None:
                    yield None
                elif isinstance(item, TextDelta):
                    if ttft_ms is None:
                        ttft_ms = int((time.monotonic() - started) * 1000)
                    yield item
                elif isinstance(item, CitationDelta):
                    yield item
                else:
                    final = item

            # Read `stop_reason` before touching `content`. On the streaming path
            # this cannot happen before text reaches the wire — deltas arrive
            # first, by construction — so the SSE layer discards the partial text
            # when a refusal lands rather than pretending the ordering held.
            stop_reason = getattr(final, "stop_reason", "") or ""
            usage = _usage_from(getattr(final, "usage", None))
            request_id = getattr(final, "_request_id", "") or ""

            yield Completed(
                stop_reason=stop_reason,
                refusal_message=_refusal_message(final) if stop_reason == "refusal" else "",
                usage=usage,
                request_id=request_id,
            )

        except Exception as exc:
            error_type = type(exc).__name__
            log.warning("llm_call_failed", error_type=error_type, model=self._model)
            raise UpstreamError(
                "The model provider did not respond.",
                error_type=error_type,
                retry_after=_retry_after(exc),
            ) from exc

        finally:
            # In a `finally`, so a timeout, a 429, a 500 or a client disconnect
            # still leaves a row. A call that failed is the one you most want a
            # record of, and it is also the one a naive implementation loses.
            #
            # This runs on the *request* thread. The worker in `_drain` touches
            # nothing but the network, precisely so this write uses the request's
            # own database connection — a ledger write from a worker thread
            # opens a second connection that cannot see the request's
            # transaction, which fails as a foreign-key violation the first time
            # anything wraps the request in one.
            ledger.record(
                session_id=session_id,
                message_id=message_id,
                purpose=purpose,
                model=self._model,
                effort=request.effort,
                backend=self.backend_name,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_creation_tokens=usage.cache_creation_tokens,
                cost_usd=cost_usd(
                    model=self._model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_creation_tokens=usage.cache_creation_tokens,
                ),
                latency_ms=int((time.monotonic() - started) * 1000),
                ttft_ms=ttft_ms,
                stop_reason=stop_reason,
                error_type=error_type,
                anthropic_request_id=request_id,
            )

    def _payload(self, request: ChatRequest) -> dict[str, Any]:
        """The request body. Every field here is deliberate; see the module docstring.

        Order is fixed for prefix-cache stability — system, then the résumé block,
        then history, then the question. Any byte change invalidates everything
        after it, which is why the system prompt is frozen and interpolation-free.
        """
        messages: list[dict[str, Any]] = []

        resume_blocks = [b.to_anthropic() for b in request.blocks if b.chunk_id is None]
        if resume_blocks:
            messages.append({"role": "user", "content": resume_blocks})
            # A user turn needs an assistant turn after it before the next user
            # turn. This one is a fixed acknowledgement, not a prefill: prefill
            # means an assistant turn that is *last* in the list, which returns
            # 400 on opus-5. An assistant turn mid-conversation is ordinary.
            messages.append({"role": "assistant", "content": "Résumé received."})

        for role, text in request.history:
            messages.append({"role": role, "content": text})

        tail: list[dict[str, Any]] = [b.to_anthropic() for b in request.blocks if b.chunk_id]
        tail.append({"type": "text", "text": request.question})
        messages.append({"role": "user", "content": tail})

        system: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": request.system,
                # ~900 tokens, comfortably over opus-5's 512-token cache
                # minimum, and frozen so the prefix is byte-identical across
                # every request in a session — and across both modes, which is
                # why the mode line is a separate uncached block below.
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if request.system_suffix:
            system.append({"type": "text", "text": request.system_suffix})

        return {
            "model": self._model,
            # thinking + visible text share this cap on opus-5.
            "max_tokens": request.max_tokens,
            "system": system,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            # `display` left at its default: thinking runs and bills identically
            # under every value, and the UI never renders reasoning — summarized
            # thinking would be text paid for and discarded.
            "output_config": {"effort": request.effort},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }


def _drain(client: Any, payload: dict[str, Any], *, heartbeat_s: float) -> Iterator[Any]:
    """Iterate the SDK stream on a worker thread; yield `None` during silence.

    A generator blocked in `next()` cannot notice that nothing has arrived, so
    the read has to happen somewhere else. The worker touches only the SDK — no
    ORM, no Django state — which keeps the ledger write (and its database
    connection) on the request thread where it belongs.

    Yields parsed events, then the final message object, then returns.
    """
    queue: Queue[tuple[str, Any]] = Queue(maxsize=64)

    def produce() -> None:
        try:
            answer_chars = 0
            with client.beta.messages.stream(**payload) as stream:
                for event in stream:
                    parsed = _parse_event(event, answer_chars=answer_chars)
                    if parsed is None:
                        continue
                    if isinstance(parsed, TextDelta):
                        answer_chars += len(parsed.text)
                    queue.put(("event", parsed))
                queue.put(("final", stream.get_final_message()))
        except BaseException as exc:
            queue.put(("error", exc))
        finally:
            queue.put(("done", None))

    thread = Thread(target=produce, name="cia-anthropic-stream", daemon=True)
    thread.start()

    while True:
        try:
            kind, item = queue.get(timeout=heartbeat_s)
        except Empty:
            yield None
            continue
        if kind in ("event", "final"):
            yield item
        elif kind == "error":
            raise item
        else:
            return


def _parse_event(event: Any, *, answer_chars: int) -> StreamEvent | None:
    """Normalise one SDK stream event.

    ── UNVERIFIED BOUNDARY ──────────────────────────────────────────────────
    The plan mandates a 30-minute spike against the real API to confirm the
    `citations_delta` / `char_location` field names before any streaming code
    was written. No API key was available, so that spike has not run and the
    field names below come from the documented shape rather than an observed
    response. `make smoke-live` performs exactly that spike and writes
    `tests/fixtures/anthropic/citations_stream.json`; until it has been run
    against a real key, treat this function as the one place in the project
    whose contract is asserted rather than measured.

    `getattr` rather than attribute access throughout, for that reason: an
    unexpected event type degrades to "no citation" — an answer without
    clickable marks — instead of a 500 mid-stream.
    """
    kind = getattr(event, "type", "")

    if kind != "content_block_delta":
        return None

    delta = getattr(event, "delta", None)
    delta_type = getattr(delta, "type", "")

    if delta_type == "text_delta":
        return TextDelta(text=getattr(delta, "text", "") or "")

    if delta_type == "citations_delta":
        citation = getattr(delta, "citation", None)
        if citation is None or getattr(citation, "type", "") != "char_location":
            return None
        return CitationDelta(
            document_index=int(getattr(citation, "document_index", 0) or 0),
            start_char_index=int(getattr(citation, "start_char_index", 0) or 0),
            end_char_index=int(getattr(citation, "end_char_index", 0) or 0),
            cited_text=getattr(citation, "cited_text", "") or "",
            answer_char=answer_chars,
        )

    return None


def _usage_from(raw: Any) -> Usage:
    if raw is None:
        return Usage()
    return Usage(
        input_tokens=int(getattr(raw, "input_tokens", 0) or 0),
        output_tokens=int(getattr(raw, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(raw, "cache_read_input_tokens", 0) or 0),
        cache_creation_tokens=int(getattr(raw, "cache_creation_input_tokens", 0) or 0),
    )


def _refusal_message(final: Any) -> str:
    """Human-readable reason for a refusal.

    `stop_details` is `None`-guarded even though `stop_reason == "refusal"`:
    the two are independent, and assuming otherwise is an `AttributeError`
    thrown while handling the one response shape you cannot re-request.
    """
    details = getattr(final, "stop_details", None)
    if details is None:
        return ""
    return str(getattr(details, "reason", "") or getattr(details, "type", "") or "")


def _retry_after(exc: Exception) -> int | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    try:
        return int(float(headers.get("retry-after", "")))
    except (TypeError, ValueError):
        return None
