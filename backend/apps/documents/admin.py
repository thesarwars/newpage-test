"""Read-only inspection of ingested documents.

`normalized_text` is deliberately absent from the list display: the admin is a
debugging surface, not a place to browse candidates' résumés.
"""

from __future__ import annotations

from django.contrib import admin

from apps.core.admin import ReadOnlyAdmin
from apps.documents.models import Document, Section


@admin.register(Document)
class DocumentAdmin(ReadOnlyAdmin):
    list_display = ("id", "kind", "ordinal", "status", "page_count", "injection_flag", "created_at")
    list_filter = ("kind", "status", "injection_flag")
    search_fields = ("id",)
    ordering = ("-created_at",)


@admin.register(Section)
class SectionAdmin(ReadOnlyAdmin):
    list_display = ("id", "document", "kind", "heading", "char_start", "char_end", "is_boilerplate")
    list_filter = ("kind", "is_boilerplate")
    ordering = ("document", "order")
