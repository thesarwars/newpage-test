"""The single writer for the call ledger.

Both backends go through here rather than each calling `LLMCall.objects.create`,
for one reason: writing the row and advancing the session's running totals are
two halves of the same fact. Split across two call sites they drift, and the
symptom is a usage meter that reads zero while the ledger shows spend — which
looks like the meter is broken rather than like a missing write.

`F()` expressions, not read-modify-write: two concurrent streams on one session
would otherwise each read the same total and each clobber the other's increment.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db.models import F

from apps.core.models import Session
from apps.observability.models import LLMCall


def record(
    *,
    session_id: Any,
    message_id: Any,
    purpose: str,
    model: str,
    backend: str,
    effort: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cost_usd: Decimal = Decimal("0"),
    latency_ms: int = 0,
    ttft_ms: int | None = None,
    stop_reason: str = "",
    error_type: str = "",
    anthropic_request_id: str = "",
) -> LLMCall:
    call = LLMCall.objects.create(
        session_id=session_id,
        message_id=message_id,
        purpose=purpose,
        model=model,
        effort=effort,
        backend=backend,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        stop_reason=stop_reason,
        error_type=error_type,
        anthropic_request_id=anthropic_request_id,
    )

    if session_id is not None:
        billed = input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens
        if billed or cost_usd:
            Session.objects.filter(pk=session_id).update(
                tokens_used=F("tokens_used") + billed,
                cost_usd=F("cost_usd") + cost_usd,
            )

    return call
