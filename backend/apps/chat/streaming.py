"""The SSE contract.

One named event per concern, so the client is a switch statement rather than a
parser:

    status · scope · sources · delta · citation · no_context · refusal · error · done

SSE over WebSockets because this is strictly one-directional, it survives every
proxy, it needs no ASGI or Redis layer, and it degrades to a readable `curl`
transcript — which is how it gets debugged.

The event *order* is a product decision, not an implementation detail. `sources`
lands before any text exists, so grounding visibly happens first and the answer
reads as derived rather than generated. That is the whole reason the retrieval
is legible instead of implied.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import structlog
from django.conf import settings
from django.db import transaction

from apps.chat import citations as citation_map
from apps.chat import context, guards
from apps.chat.models import (
    Citation,
    Message,
    MessageStatus,
    Mode,
    RefusalReason,
    RetrievalTrace,
    Role,
)
from apps.chat.prompts import PROMPT_VERSION
from apps.core.models import Session
from apps.observability.models import LLMCall
from apps.rag import pipeline
from llm import backends, budget
from llm.gateway import UpstreamError
from llm.types import CitationDelta, Completed, TextDelta

log = structlog.get_logger(__name__)

SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    # `no-transform` matters as much as `no-cache`: a proxy that gzips this
    # buffers it to compress it, which is the same failure as caching it.
    "Cache-Control": "no-cache, no-transform",
    # nginx buffers proxied responses by default, which turns a token-by-token
    # stream into one delivery at the end — the exact failure this design exists
    # to avoid, and one that only appears once it is behind a proxy.
    "X-Accel-Buffering": "no",
}

# Set by the server, never by the application. WSGI asserts on these, so
# `Connection: keep-alive` — which every SSE tutorial includes — turns the
# endpoint into a 500 the moment it runs on a real server. Django's test client
# never reaches `start_response`, so the suite cannot catch it; this list and
# the test that reads it are what stands in for that.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


def sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


def ping() -> bytes:
    return b": ping\n\n"


def stream_chat(
    *,
    session: Session,
    question: str,
    job_ids: list[str] | None = None,
    mode: str = Mode.ANALYSIS,
) -> Iterator[bytes]:
    """One assistant turn, start to finish.

    A generator rather than a coroutine: `StreamingHttpResponse` consumes an
    iterator, WSGI is what this deploys on, and nothing here is I/O-bound enough
    for async to buy anything a thread does not.
    """
    started = time.monotonic()

    yield sse("status", {"phase": "resolving", "detail": "Working out which documents to read…"})

    retrieval_started = time.monotonic()
    result = pipeline.retrieve(session=session, message=question, explicit_job_ids=job_ids)
    retrieval_ms = int((time.monotonic() - retrieval_started) * 1000)

    live = backends.get_backend()
    yield sse(
        "scope",
        {
            "job_ids": result.scope.document_ids,
            "intent": result.intent.value,
            "resolved_from": result.scope.resolved_from,
            "demo_mode": live.backend_name != "anthropic",
        },
    )

    # Context is assembled *before* the user's turn is persisted. Otherwise the
    # question would appear in its own history.
    request = context.build(
        session=session,
        question=question,
        chunks=result.chunks,
        mode=mode,
        effort=settings.CIA_CHAT_EFFORT,
    )

    Message.objects.create(
        session=session,
        role=Role.USER,
        content=question,
        mode=mode,
        intent=result.intent.value,
        scope_job_ids=result.scope.document_ids,
    )
    assistant = Message.objects.create(
        session=session,
        role=Role.ASSISTANT,
        content="",
        mode=mode,
        intent=result.intent.value,
        scope_job_ids=result.scope.document_ids,
        status=MessageStatus.STREAMING,
        grounding_max_score=result.trace.max_score,
        prompt_version=PROMPT_VERSION,
    )
    _save_trace(assistant, result.trace, timings={"retrieval_total": retrieval_ms})

    # ── refusals and empty context, both free ────────────────────────────────

    if refusal := guards.check(question, intent=result.intent):
        _finish(assistant, status=MessageStatus.REFUSED, refusal_reason=refusal.reason)
        yield sse(
            "refusal",
            {
                "reason": refusal.reason,
                "message": refusal.message,
                "suggestion": refusal.suggestion,
            },
        )
        yield _done(assistant, started=started, citations=0, max_score=0.0, low_evidence=False)
        return

    if not result.has_context:
        # Zero tokens spent. A model asked to answer from nothing produces
        # something plausible, which is the worst available output here.
        _finish(assistant, status=MessageStatus.NO_CONTEXT)
        yield sse(
            "no_context",
            {
                "reason": _no_context_reason(result),
                "suggestions": _suggestions(result),
            },
        )
        yield _done(assistant, started=started, citations=0, max_score=0.0, low_evidence=False)
        return

    yield sse("sources", {"chunks": [_source(c) for c in result.chunks]})
    yield sse("status", {"phase": "generating", "detail": "Reading the passages…"})

    # ── generation ───────────────────────────────────────────────────────────

    documents = citation_map.load_documents(request.blocks)
    resolver = citation_map.CitationResolver(request.blocks, documents)

    text_parts: list[str] = []
    resolved: list[citation_map.ResolvedCitation] = []
    stop_reason = ""
    refused = False
    ttft_ms: int | None = None

    try:
        source = live.stream(
            request,
            session_id=session.pk,
            message_id=assistant.pk,
        )
        for event in source:
            # The backend emits `None` when the upstream has gone quiet — it is
            # the only layer that can tell. Translate it into the comment frame
            # that keeps proxies from reaping the connection.
            if event is None:
                yield ping()
                continue

            if isinstance(event, TextDelta):
                if ttft_ms is None:
                    ttft_ms = int((time.monotonic() - started) * 1000)
                text_parts.append(event.text)
                yield sse("delta", {"text": event.text})

            elif isinstance(event, CitationDelta):
                citation = resolver.resolve(event)
                if citation is None:
                    continue
                resolved.append(citation)
                yield sse("citation", _citation_payload(citation))

            elif isinstance(event, Completed):
                stop_reason = event.stop_reason
                if stop_reason == "refusal":
                    refused = True
                    yield sse(
                        "refusal",
                        {
                            "reason": RefusalReason.MODEL_REFUSAL,
                            "message": event.refusal_message
                            or "I can't help with that particular request.",
                            "suggestion": "Try asking about your fit for one of the roles you've uploaded.",
                        },
                    )

    except budget.BudgetExhaustedError as exc:
        _finish(
            assistant, status=MessageStatus.ERROR, refusal_reason=RefusalReason.BUDGET_EXHAUSTED
        )
        log.warning("chat_budget_exhausted", spent=str(exc.spent), ceiling=str(exc.ceiling))
        yield sse(
            "error",
            {
                "code": "budget_exhausted",
                "message": "The daily model budget for this deployment is spent.",
                "hint": "Everything that doesn't need the model — retrieval, the fit board, the gap matrix — still works.",
            },
        )
        return

    except UpstreamError as exc:
        _finish(assistant, status=MessageStatus.ERROR)
        yield sse(
            "error",
            {
                "code": "upstream_error",
                "message": str(exc),
                "retry_after": exc.retry_after,
            },
        )
        return

    answer = "".join(text_parts)

    if refused:
        # A mid-stream refusal means partial text is already on the wire — that
        # ordering is not avoidable while streaming, and the design accommodates
        # it rather than pretending otherwise. The client discards the partial
        # text and renders the refusal card; the server discards it too, so a
        # page reload doesn't resurrect an orphaned half-answer.
        _finish(
            assistant,
            status=MessageStatus.REFUSED,
            refusal_reason=RefusalReason.MODEL_REFUSAL,
            stop_reason=stop_reason,
        )
        yield _done(assistant, started=started, citations=0, max_score=0.0, low_evidence=False)
        return

    _persist(assistant, answer=answer, citations=resolved, stop_reason=stop_reason)

    if resolver.dropped:
        log.warning("citations_dropped", count=resolver.dropped, message_id=str(assistant.pk))

    yield _done(
        assistant,
        started=started,
        citations=len(resolved),
        max_score=result.trace.max_score,
        # Labelled, not suppressed: an answer with no citations may still be
        # correct, and hiding it because the extractor missed is worse than an
        # honest badge on it.
        low_evidence=result.low_evidence or not resolved,
        ttft_ms=ttft_ms,
    )


# ── plumbing ─────────────────────────────────────────────────────────────────


def _source(chunk: Any) -> dict[str, Any]:
    """One source chip. Preview only — never the full chunk."""
    return {
        "id": str(chunk.id),
        "doc_id": str(chunk.document_id),
        "doc_label": chunk.document.display_label,
        "kind": chunk.document.kind,
        "section": (chunk.section.heading if chunk.section else "") or "",
        "preview": chunk.text[:180],
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
    }


def _citation_payload(citation: citation_map.ResolvedCitation) -> dict[str, Any]:
    return {
        "index": citation.index,
        "answer_char": citation.answer_char,
        "chunk_id": citation.chunk_id,
        "doc_id": citation.document_id,
        "char_start": citation.doc_char_start,
        "char_end": citation.doc_char_end,
        "cited_text": citation.cited_text,
    }


def _done(
    message: Message,
    *,
    started: float,
    citations: int,
    max_score: float,
    low_evidence: bool,
    ttft_ms: int | None = None,
) -> bytes:
    # Usage is read back from the ledger rather than recomputed here. The ledger
    # is the number the budget enforces and the usage panel displays; a second
    # calculation would be a second thing to keep in agreement.
    call = LLMCall.objects.filter(message_id=message.pk).order_by("-created_at").first()
    usage = {
        "input_tokens": call.input_tokens if call else 0,
        "output_tokens": call.output_tokens if call else 0,
        "cache_read_tokens": call.cache_read_tokens if call else 0,
        "cost_usd": f"{call.cost_usd:.6f}" if call else "0.000000",
    }
    return sse(
        "done",
        {
            "message_id": str(message.pk),
            "status": message.status,
            "usage": usage,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "ttft_ms": ttft_ms if ttft_ms is not None else (call.ttft_ms if call else None),
            "grounding": {
                "citations": citations,
                "max_score": round(max_score, 4),
                "low_evidence": low_evidence,
            },
        },
    )


def _persist(
    message: Message,
    *,
    answer: str,
    citations: list[citation_map.ResolvedCitation],
    stop_reason: str,
) -> None:
    with transaction.atomic():
        message.content = answer
        message.status = MessageStatus.COMPLETE
        message.stop_reason = stop_reason
        message.save(update_fields=["content", "status", "stop_reason", "updated_at"])

        Citation.objects.bulk_create(
            [
                Citation(
                    message=message,
                    index=c.index,
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    doc_char_start=c.doc_char_start,
                    doc_char_end=c.doc_char_end,
                    cited_text=c.cited_text,
                    answer_char=c.answer_char,
                )
                for c in citations
            ]
        )


def _finish(
    message: Message,
    *,
    status: str,
    refusal_reason: str = "",
    stop_reason: str = "",
) -> None:
    message.status = status
    message.refusal_reason = refusal_reason
    message.stop_reason = stop_reason
    message.content = ""
    message.save(update_fields=["status", "refusal_reason", "stop_reason", "content", "updated_at"])


def _save_trace(
    message: Message, trace: pipeline.RetrievalTrace, *, timings: dict[str, int]
) -> None:
    RetrievalTrace.objects.create(
        message=message,
        query=trace.query,
        expanded_query=trace.expanded_query,
        scope_job_ids=trace.scope_document_ids,
        resolved_from=trace.resolved_from,
        dense_hits=trace.dense_hits,
        lexical_hits=trace.lexical_hits,
        fused=trace.fused,
        selected_chunk_ids=trace.selected_chunk_ids,
        anchors_applied=trace.anchors_applied,
        quota_applied=trace.quota_applied,
        max_score=trace.max_score,
        context_chars=trace.context_chars,
        timings_ms={**trace.timings_ms, **timings},
    )


def _no_context_reason(result: pipeline.RetrievalResult) -> str:
    if not result.scope.document_ids:
        return "There's nothing uploaded yet to answer from."
    return (
        "Nothing in your documents is close enough to that question for me to "
        "answer it honestly. I'd rather say that than guess."
    )


def _suggestions(result: pipeline.RetrievalResult) -> list[str]:
    if not result.scope.document_ids:
        return ["Upload your résumé", "Add a job description you're considering"]
    return [
        "What am I missing for this role?",
        "Which of my projects is most relevant here?",
        "How does my experience line up with the requirements?",
    ]
