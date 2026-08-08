"""The LLM call ledger.

Every Anthropic call writes exactly one row here, in a `finally` block, so
timeouts, 4xx, 5xx and refusals are recorded with an `error_type` instead of
vanishing. A call that failed is the one you most want a record of.

This table *is* the observability story for this project. Metrics in an endpoint
nobody scrapes prove nothing in a take-home; a relational ledger can be read from
the admin, joined to the message that caused it, and rendered under the answer
itself — which is the difference between observability you can demonstrate and
observability you can describe.

It survives session deletion, anonymised. "Delete everything" must remove the
user's documents; it must not erase the fact that spend occurred.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.core.models import Session, TimeStamped


class CallPurpose(models.TextChoices):
    CHAT = "chat", "Chat"
    CLASSIFY = "classify", "Classification"
    REQUIREMENT_EXTRACT = "requirement_extract", "Requirement extraction"
    REQUIREMENT_MATCH = "requirement_match", "Requirement matching"
    INTERVIEW_PREP = "interview_prep", "Interview prep"
    JUDGE = "judge", "Eval judge"


class LLMCall(TimeStamped):
    # SET_NULL, not CASCADE: purging a session anonymises its calls rather than
    # deleting them. No content was ever stored here, only counts and costs.
    session = models.ForeignKey(
        Session, on_delete=models.SET_NULL, null=True, blank=True, related_name="llm_calls"
    )
    message_id = models.UUIDField(null=True, blank=True, db_index=True)

    purpose = models.CharField(max_length=24, choices=CallPurpose.choices)
    model = models.CharField(max_length=64)
    effort = models.CharField(max_length=16, blank=True)
    backend = models.CharField(max_length=16, default="anthropic")

    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cache_read_tokens = models.PositiveIntegerField(default=0)
    cache_creation_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("0"))

    latency_ms = models.PositiveIntegerField(default=0)
    # Time to first token. The metric the streaming UX is designed around, and
    # therefore the one worth measuring rather than assuming.
    ttft_ms = models.PositiveIntegerField(null=True, blank=True)

    stop_reason = models.CharField(max_length=32, blank=True)
    error_type = models.CharField(max_length=64, blank=True)
    anthropic_request_id = models.CharField(max_length=128, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["session", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.purpose} {self.model} ${self.cost_usd}"

    @property
    def cache_hit_ratio(self) -> float:
        """Share of input served from cache.

        Surfaced because cache misses are *silent*: one interpolated timestamp in
        the system prompt costs 10x on input with no error anywhere. A ratio near
        zero across a session is the symptom.
        """
        total = self.input_tokens + self.cache_read_tokens
        return self.cache_read_tokens / total if total else 0.0
