"""Document and Section.

`normalized_text` lives on the row rather than being re-derived on read. It is
the coordinate space every citation offset points into, so it has to be stable
for the life of the document — recomputing it after a library upgrade would
silently move every stored offset.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import Session, SessionScopedModel
from apps.documents.chunking.sections import SectionKind
from apps.documents.validators import MAX_PAGES

MAX_JOBS_PER_SESSION = 10


class DocumentKind(models.TextChoices):
    RESUME = "resume", "Résumé"
    JOB = "job", "Job description"


class DocumentStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    PARSING = "parsing", "Parsing"
    CHUNKING = "chunking", "Chunking"
    EMBEDDING = "embedding", "Embedding"
    ANALYZING = "analyzing", "Analyzing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class Document(SessionScopedModel):
    kind = models.CharField(max_length=16, choices=DocumentKind.choices)
    # The "#2" in "how do I match Job #2?". Stable per session, renumbered on
    # delete, and resolved without an LLM call (M4's scope resolver).
    ordinal = models.PositiveSmallIntegerField(default=0)

    label = models.CharField(max_length=200, blank=True)
    company = models.CharField(max_length=200, blank=True)

    original_filename = models.CharField(max_length=255, blank=True)
    file = models.FileField(upload_to="documents/", blank=True, null=True)
    mime_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
    page_count = models.PositiveSmallIntegerField(default=0)

    status = models.CharField(
        max_length=16, choices=DocumentStatus.choices, default=DocumentStatus.QUEUED
    )
    error_code = models.CharField(max_length=32, blank=True)
    error_detail = models.TextField(blank=True)

    # The canonical coordinate space. Every char_start/char_end in the system —
    # sections, chunks, and rebased Anthropic citation offsets — indexes here.
    normalized_text = models.TextField(blank=True)
    text_sha256 = models.CharField(max_length=64, blank=True)
    embedding_model = models.CharField(max_length=100, blank=True)

    injection_flag = models.BooleanField(default=False)
    injection_reasons = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("kind", "ordinal")
        constraints = [
            models.UniqueConstraint(
                fields=["session", "kind", "ordinal"],
                name="unique_ordinal_per_kind_per_session",
            ),
            models.CheckConstraint(
                condition=models.Q(page_count__lte=MAX_PAGES),
                name="page_count_within_cap",
            ),
        ]
        indexes = [models.Index(fields=["session", "kind"])]

    def __str__(self) -> str:
        return self.display_label

    @property
    def display_label(self) -> str:
        if self.label:
            return self.label
        if self.kind == DocumentKind.RESUME:
            return "Résumé"
        return f"Job #{self.ordinal}"

    @property
    def is_ready(self) -> bool:
        return self.status == DocumentStatus.READY


class Section(SessionScopedModel):
    """A detected span of the parent document's `normalized_text`.

    Session-scoped as well as document-scoped: carrying the tenant column here
    means retrieval filters never need a join to enforce isolation, and the
    tenancy guard test covers this model for free.
    """

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="sections")
    heading = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=24, choices=[(k.value, k.name.title()) for k in SectionKind])
    char_start = models.PositiveIntegerField()
    char_end = models.PositiveIntegerField()
    is_boilerplate = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("document", "order")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(char_end__gte=models.F("char_start")),
                name="section_span_is_not_inverted",
            ),
        ]
        indexes = [models.Index(fields=["document", "order"])]

    def __str__(self) -> str:
        return f"{self.heading or self.kind} [{self.char_start}:{self.char_end}]"

    def text_from(self, normalized_text: str) -> str:
        return normalized_text[self.char_start : self.char_end]


def next_ordinal(session: Session, kind: str) -> int:
    """Next free ordinal for a kind within a session.

    Résumés are always 0 (there is only ever one). Jobs count from 1, because
    the product says "Job #1" and a user who reads "Job #0" will assume a bug.
    """
    if kind == DocumentKind.RESUME:
        return 0
    highest = (
        Document.objects.for_session(session)
        .filter(kind=kind)
        .aggregate(models.Max("ordinal"))["ordinal__max"]
    )
    return (highest or 0) + 1
