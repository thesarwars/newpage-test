"""Chat endpoints: the stream, the history, and the receipts."""

from __future__ import annotations

from typing import Any

import structlog
from django.http import StreamingHttpResponse
from rest_framework.decorators import api_view, renderer_classes, throttle_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from apps.chat import streaming
from apps.chat.models import Citation, Message, Mode, RetrievalTrace
from apps.chat.throttles import ChatBurstThrottle, ChatSustainedThrottle
from apps.core.errors import ApiError, NotFoundError, SessionRequiredError
from apps.core.models import Session
from apps.observability.models import LLMCall
from llm import backends, budget

log = structlog.get_logger(__name__)

MAX_QUESTION_CHARS = 2000


def _current(request: Request) -> Session:
    session: Session | None = getattr(request, "cia_session", None)
    if session is None:
        raise SessionRequiredError()
    return session


@api_view(["POST"])
# JSON stays first so it remains the default for the error envelope, which is a
# real DRF Response and does need rendering.
@renderer_classes([JSONRenderer, streaming.EventStreamRenderer])
@throttle_classes([ChatBurstThrottle, ChatSustainedThrottle])
def chat(request: Request) -> StreamingHttpResponse:
    """`POST /api/v1/chat/` — Server-Sent Events.

    Returns a `StreamingHttpResponse` rather than a DRF `Response`; DRF passes
    non-`Response` results through untouched, so the throttles and the error
    envelope above still apply while the body streams.
    """
    session = _current(request)
    data = request.data if isinstance(request.data, dict) else {}

    question = str(data.get("message", "")).strip()
    if not question:
        raise ApiError(
            "Ask a question first.",
            error_code="empty_message",
            hint="Send a `message` field with your question.",
        )
    if len(question) > MAX_QUESTION_CHARS:
        raise ApiError(
            f"That question is longer than the {MAX_QUESTION_CHARS}-character limit.",
            error_code="message_too_long",
            hint="Shorten it, or upload the text as a document instead.",
        )

    scope = data.get("scope") or {}
    job_ids = scope.get("job_ids") if isinstance(scope, dict) else None
    mode = scope.get("mode") if isinstance(scope, dict) else None
    if mode not in Mode.values:
        mode = Mode.ANALYSIS

    session.touch()

    response = StreamingHttpResponse(
        streaming.stream_chat(
            session=session,
            question=question,
            job_ids=[str(j) for j in job_ids] if isinstance(job_ids, list) else None,
            mode=str(mode),
        ),
        content_type="text/event-stream",
    )
    for header, value in streaming.SSE_HEADERS.items():
        response[header] = value
    return response


@api_view(["GET"])
def messages(request: Request) -> Response:
    """`GET /api/v1/chat/messages/` — history with citations, for a page reload.

    Citations come back attached to their message rather than as a flat list, so
    the client can re-splice marks without a second request or a join it has to
    write itself.
    """
    session = _current(request)
    rows = list(Message.objects.for_session(session).order_by("created_at"))

    by_message: dict[Any, list[dict[str, Any]]] = {}
    for citation in Citation.objects.filter(message__in=rows).order_by("index"):
        by_message.setdefault(citation.message_id, []).append(_citation(citation))

    return Response(
        {
            "messages": [_message(m, by_message.get(m.pk, [])) for m in rows],
            "demo_mode": not backends.has_live_backend(),
        }
    )


@api_view(["GET"])
def trace(request: Request, message_id: str) -> Response:
    """`GET /api/v1/traces/{message_id}/` — why those passages, and what it cost."""
    session = _current(request)
    message = Message.objects.for_session(session).filter(pk=message_id).first()
    if message is None:
        raise NotFoundError()

    row: RetrievalTrace | None = RetrievalTrace.objects.filter(message=message).first()
    calls = LLMCall.objects.filter(message_id=message.pk).order_by("created_at")

    return Response(
        {
            "message_id": str(message.pk),
            "retrieval": _trace(row) if row else None,
            "llm_calls": [_call(c) for c in calls],
        }
    )


@api_view(["GET"])
def usage(request: Request) -> Response:
    """`GET /api/v1/usage/` — session totals plus the last 20 calls."""
    session = _current(request)
    calls = LLMCall.objects.filter(session=session).order_by("-created_at")[:20]
    return Response(
        {
            "session": {
                "tokens_used": session.tokens_used,
                "cost_usd": f"{session.cost_usd:.6f}",
            },
            "daily": {
                "spent_usd": f"{budget.spent_today():.6f}",
                "ceiling_usd": f"{budget.ceiling():.2f}",
                "remaining_usd": f"{budget.remaining():.6f}",
            },
            "backend": "anthropic" if backends.has_live_backend() else "fake",
            "calls": [_call(c) for c in calls],
        }
    )


# ── serialisers ──────────────────────────────────────────────────────────────


def _message(message: Message, citations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": str(message.pk),
        "role": message.role,
        "content": message.content,
        "mode": message.mode,
        "intent": message.intent,
        "scope_job_ids": message.scope_job_ids,
        "status": message.status,
        "refusal_reason": message.refusal_reason,
        "grounding": {
            "max_score": round(message.grounding_max_score, 4),
            "citations": len(citations),
            "low_evidence": message.role == "assistant"
            and message.status == "complete"
            and not citations,
        },
        "created_at": message.created_at.isoformat(),
        "citations": citations,
    }


def _citation(citation: Citation) -> dict[str, Any]:
    return {
        "index": citation.index,
        "answer_char": citation.answer_char,
        "chunk_id": str(citation.chunk_id) if citation.chunk_id else None,
        "doc_id": str(citation.document_id),
        "char_start": citation.doc_char_start,
        "char_end": citation.doc_char_end,
        "cited_text": citation.cited_text,
    }


def _trace(row: RetrievalTrace) -> dict[str, Any]:
    return {
        "query": row.query,
        "expanded_query": row.expanded_query,
        "scope_job_ids": row.scope_job_ids,
        "resolved_from": row.resolved_from,
        "dense_hits": row.dense_hits,
        "lexical_hits": row.lexical_hits,
        "selected_chunk_ids": row.selected_chunk_ids,
        "anchors_applied": row.anchors_applied,
        "quota_applied": row.quota_applied,
        "max_score": row.max_score,
        "context_chars": row.context_chars,
        "timings_ms": row.timings_ms,
    }


def _call(call: LLMCall) -> dict[str, Any]:
    return {
        "id": str(call.pk),
        "purpose": call.purpose,
        "model": call.model,
        "backend": call.backend,
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "cache_read_tokens": call.cache_read_tokens,
        "cache_hit_ratio": round(call.cache_hit_ratio, 3),
        "cost_usd": f"{call.cost_usd:.6f}",
        "latency_ms": call.latency_ms,
        "ttft_ms": call.ttft_ms,
        "stop_reason": call.stop_reason,
        "error_type": call.error_type,
        "created_at": call.created_at.isoformat(),
    }
