"""Admin for the call ledger — read-only, on purpose.

This table is the audit trail for spend. Editable audit records are not audit
records, so every field is read-only and nothing can be added by hand. Deletion
stays available: the 7-day purge needs it.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.observability.models import LLMCall


@admin.register(LLMCall)
class LLMCallAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "purpose",
        "model",
        "backend",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "latency_ms",
        "ttft_ms",
        "stop_reason",
        "error_type",
    )
    list_filter = ("purpose", "backend", "model", "stop_reason", "error_type")
    search_fields = ("anthropic_request_id",)
    date_hierarchy = "created_at"

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> list[str]:
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
