"""Chat persistence: the message, its citations, and how it was retrieved.

The three tables are separate on purpose. A `Message` is what the user sees; a
`Citation` is a clickable span with coordinates into a document; a
`RetrievalTrace` is the evidence that the answer came from somewhere. Folding
the last two into JSON on the message would make the trace drawer a blob
renderer and would put the citation offsets out of reach of a database
constraint — and those offsets are the one thing in this system that is either
exactly right or visibly broken.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import SessionScopedModel, TimeStamped
from apps.documents.models import Chunk, Document


class Role(models.TextChoices):
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"


class Mode(models.TextChoices):
    ANALYSIS = "analysis", "Analysis"
    INTERVIEW = "interview", "Interview prep"


class MessageStatus(models.TextChoices):
    STREAMING = "streaming", "Streaming"
    COMPLETE = "complete", "Complete"
    REFUSED = "refused", "Refused"
    NO_CONTEXT = "no_context", "No context"
    ERROR = "error", "Error"


class RefusalReason(models.TextChoices):
    OUT_OF_SCOPE = "out_of_scope", "Out of scope"
    FABRICATION_REQUEST = "fabrication_request", "Fabrication request"
    MODEL_REFUSAL = "model_refusal", "Model refusal"
    RATE_LIMITED = "rate_limited", "Rate limited"
    BUDGET_EXHAUSTED = "budget_exhausted", "Budget exhausted"


class Message(SessionScopedModel):
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField(blank=True)
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.ANALYSIS)

    intent = models.CharField(max_length=24, blank=True)
    scope_job_ids = models.JSONField(default=list, blank=True)

    status = models.CharField(
        max_length=16, choices=MessageStatus.choices, default=MessageStatus.COMPLETE
    )
    refusal_reason = models.CharField(max_length=32, choices=RefusalReason.choices, blank=True)
    stop_reason = models.CharField(max_length=32, blank=True)

    # Best cosine similarity in the retrieved set. Drives the "low evidence"
    # badge — an answer the system is less sure it can support says so rather
    # than reading identically to one it can.
    grounding_max_score = models.FloatField(default=0.0)
    # Stamped per message, not read from settings at display time: a prompt
    # change must not silently relabel answers produced by the old one.
    prompt_version = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [models.Index(fields=["session", "created_at"])]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:40]}"


class Citation(TimeStamped):
    """One `[n]` mark: a span of a document, and where it sits in the answer.

    Offsets are stored in *document* coordinates (into `Document.normalized_text`),
    not block-relative ones. Block coordinates are an artefact of how the request
    happened to be assembled; the evidence panel needs a location in the document
    the user uploaded, and translating once at write time means every reader
    downstream — panel, tests, trace drawer — agrees.

    `chunk` is SET_NULL: deleting a document's chunks must not delete the
    citation history of answers that already quoted them.
    """

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="citations")
    index = models.PositiveSmallIntegerField()

    chunk = models.ForeignKey(Chunk, on_delete=models.SET_NULL, null=True, blank=True)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="+")

    doc_char_start = models.PositiveIntegerField()
    doc_char_end = models.PositiveIntegerField()
    cited_text = models.TextField()

    # Offset into the assistant's text where the mark belongs. Only knowable
    # while the stream is in flight, so it is recorded there rather than
    # reconstructed by searching the finished answer for the quote.
    answer_char = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("message", "index")
        constraints = [
            models.UniqueConstraint(fields=["message", "index"], name="unique_citation_index"),
            models.CheckConstraint(
                condition=models.Q(doc_char_end__gt=models.F("doc_char_start")),
                name="citation_span_non_empty",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.index}] {self.document_id}[{self.doc_char_start}:{self.doc_char_end}]"


class RetrievalTrace(TimeStamped):
    """Why these passages, in this order.

    Persisted rather than logged because "the answer was bad" has to become "the
    relevant chunk ranked 19th in dense and lexical never fired" *after the
    fact*, from a message id — not by re-running the query and hoping it
    reproduces.

    Carries chunk ids and scores. Never chunk text: this row is rendered in a
    drawer, screenshotted, and pasted into bug reports, and a résumé line has no
    business travelling that way.
    """

    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="trace")

    query = models.TextField()
    expanded_query = models.TextField(blank=True)
    scope_job_ids = models.JSONField(default=list, blank=True)
    resolved_from = models.CharField(max_length=64, blank=True)

    dense_hits = models.JSONField(default=list, blank=True)
    lexical_hits = models.JSONField(default=list, blank=True)
    fused = models.JSONField(default=list, blank=True)
    selected_chunk_ids = models.JSONField(default=list, blank=True)

    anchors_applied = models.JSONField(default=list, blank=True)
    quota_applied = models.BooleanField(default=False)
    max_score = models.FloatField(default=0.0)
    context_chars = models.PositiveIntegerField(default=0)
    timings_ms = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"trace for {self.message_id}"
