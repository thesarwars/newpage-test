"""Requirements extracted from a job description, and how the résumé matches them.

These rows are what make gap analysis possible at all. A top-k retriever asked
"what am I missing?" returns the chunks most *similar* to the question — which
are, by construction, the skills the candidate already has. **Vector search
cannot retrieve absence.** So the gap is computed by iterating extracted
requirements and checking each against résumé evidence, which is a set
difference, not a similarity search.

`source` records which extractor produced each row. Deterministic runs with no
API key; the LLM pass upgrades it in place when one is present. Keeping the
provenance on the row means the UI can say which one a user is looking at, and
the eval can quantify the gap between them.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import SessionScopedModel
from apps.documents.models import Chunk, Document


class ExtractorSource(models.TextChoices):
    DETERMINISTIC = "deterministic", "Deterministic (no API key)"
    LLM = "llm", "Claude"


class RequirementCategory(models.TextChoices):
    HARD_SKILL = "hard_skill", "Hard skill"
    TOOL = "tool", "Tool"
    DOMAIN = "domain", "Domain"
    SOFT_SKILL = "soft_skill", "Soft skill"
    CREDENTIAL = "credential", "Credential"
    SENIORITY = "seniority", "Seniority"


class MatchStatus(models.TextChoices):
    STRONG = "strong", "Strong"
    PARTIAL = "partial", "Partial"
    MISSING = "missing", "Missing"


class Requirement(SessionScopedModel):
    """One thing a posting asks for."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="requirements")
    text = models.TextField()
    skill = models.CharField(max_length=120, db_index=True)
    category = models.CharField(
        max_length=16,
        choices=RequirementCategory.choices,
        default=RequirementCategory.HARD_SKILL,
    )
    # "5+ years of Go" is a must-have; "familiarity with Kubernetes is a plus"
    # is not. Treating the second as required inflates every gap list with things
    # the employer explicitly called optional.
    must_have = models.BooleanField(default=True)

    # Span in the *job document's* normalized_text, so the UI can show the
    # requirement highlighted in its original context.
    evidence_char_start = models.PositiveIntegerField(default=0)
    evidence_char_end = models.PositiveIntegerField(default=0)

    order = models.PositiveSmallIntegerField(default=0)
    source = models.CharField(
        max_length=16, choices=ExtractorSource.choices, default=ExtractorSource.DETERMINISTIC
    )

    class Meta:
        ordering = ("document", "order")
        indexes = [models.Index(fields=["document", "order"])]

    def __str__(self) -> str:
        return f"{self.skill} ({'must' if self.must_have else 'nice'})"


class RequirementMatch(SessionScopedModel):
    """Whether the résumé evidences a requirement."""

    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name="matches")
    resume_document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="requirement_matches"
    )
    status = models.CharField(max_length=8, choices=MatchStatus.choices)
    rationale = models.CharField(max_length=280, blank=True)
    evidence_chunks = models.ManyToManyField(Chunk, blank=True, related_name="matches")
    confidence = models.FloatField(default=0.0)
    source = models.CharField(
        max_length=16, choices=ExtractorSource.choices, default=ExtractorSource.DETERMINISTIC
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["requirement", "resume_document"], name="unique_match_per_requirement"
            )
        ]

    def __str__(self) -> str:
        return f"{self.requirement.skill}: {self.status}"
