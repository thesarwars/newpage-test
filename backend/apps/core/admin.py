"""Read-only admin.

The admin is free inspection tooling, which is most of why Django earns its keep
here — but it is deliberately read-only. There is no workflow in this app that a
human should perform by hand-editing a row, and an editable admin over session
data is a way to corrupt state that the API maintains invariants on.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.core.models import Session


# Not `admin.ModelAdmin[Session]`: django-stubs makes ModelAdmin generic for
# type-checking only, and subscripting it is a runtime TypeError.
class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Session)
class SessionAdmin(ReadOnlyAdmin):
    list_display = ("id", "created_at", "expires_at", "tokens_used", "cost_usd", "demo_seeded")
    list_filter = ("demo_seeded",)
    # Not `token`: it is a bearer credential, and an admin search box is a
    # perfectly good way to leak one into a browser history.
    search_fields = ("id",)
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
