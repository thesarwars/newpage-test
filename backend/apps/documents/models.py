"""Document and Section.

`normalized_text` lives on the row rather than being re-derived on read. It is
the coordinate space every citation offset points into, so it has to be stable
for the life of the document — recomputing it after a library upgrade would
silently move every stored offset.
"""

from __future__ import annotations

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from pgvector.django import HnswIndex, VectorField

from apps.core.models import Session, SessionScopedModel
from apps.documents.chunking.sections import SectionKind
from apps.documents.validators import MAX_PAGES

# bge-small-en-v1.5. Fixed at the schema level on purpose: changing the embedding
# model changes this number, which forces a migration — and a migration is a much
# better place to be reminded that every stored vector needs reindexing than a
# silent dimension mismatch at query time.
EMBEDDING_DIMENSIONS = 384

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


class Chunk(SessionScopedModel):
    """A retrievable span of a document.

    `session` is denormalised onto this row deliberately. Every retrieval query
    filters by tenant, and carrying the column here means that filter is a
    predicate on the table being scanned rather than a join — which matters
    because the alternative is an ANN index scan that finds another tenant's
    neighbours and then discards them.
    """

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    section = models.ForeignKey(
        Section, on_delete=models.SET_NULL, null=True, blank=True, related_name="chunks"
    )
    ordinal = models.PositiveIntegerField()

    # Raw span of normalized_text. `normalized_text[char_start:char_end] == text`
    # is the invariant the whole citation feature rests on.
    text = models.TextField()
    char_start = models.PositiveIntegerField()
    char_end = models.PositiveIntegerField()

    # What is actually encoded: breadcrumb + "\n" + text. Stored rather than
    # rebuilt at query time so that what was embedded is auditable — if
    # retrieval misbehaves, the first question is "what did the encoder see?"
    embed_text = models.TextField()
    token_count = models.PositiveSmallIntegerField(default=0)

    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True)
    search_vector = SearchVectorField(null=True, blank=True)

    is_boilerplate = models.BooleanField(default=False)
    injection_flag = models.BooleanField(default=False)

    class Meta:
        ordering = ("document", "ordinal")
        constraints = [
            models.UniqueConstraint(
                fields=["document", "ordinal"], name="unique_chunk_ordinal_per_document"
            ),
            models.CheckConstraint(
                condition=models.Q(char_end__gt=models.F("char_start")),
                name="chunk_span_is_not_empty",
            ),
        ]
        indexes = [
            # HNSW over cosine distance. m/ef_construction are the pgvector
            # defaults; ef_search is set per query (apps/rag) because it trades
            # recall against latency at read time, not write time.
            HnswIndex(
                name="chunk_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
            # The lexical arm of hybrid retrieval. Same table, same query plan,
            # so RRF fuses two rankings without a second datastore.
            GinIndex(name="chunk_search_vector_gin", fields=["search_vector"]),
            models.Index(fields=["session", "document"], name="chunk_session_document"),
        ]

    def __str__(self) -> str:
        return f"{self.document_id}#{self.ordinal} [{self.char_start}:{self.char_end}]"


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
