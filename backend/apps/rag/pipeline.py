"""The retrieval orchestrator.

    resolve scope → route intent → expand query → dense ∥ lexical
                  → RRF → quota → anchors → floor → context

Explicit Python, no framework. Every stage is a named function with its own
tests, and the trace this emits records what each one did — which is what turns
"the answer was bad" into "the relevant chunk ranked 19th in dense and lexical
never fired", a statement you can act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from apps.analysis.models import Requirement
from apps.core.models import Session
from apps.documents.models import Chunk, Document, DocumentKind, Section
from apps.rag import dense, fusion, lexical
from apps.rag.embeddings import get_embedder
from apps.rag.resolver import JobRef, Scope, resolve
from apps.rag.router import Intent, anchor_sections, expands_query, route

log = structlog.get_logger(__name__)


@dataclass
class RetrievalTrace:
    """Everything a human needs to explain one retrieval.

    Persisted per message in M5 and rendered under every answer. It exists
    because the alternative — re-running the query by hand and hoping it
    reproduces — is not debugging.
    """

    query: str
    expanded_query: str = ""
    intent: str = ""
    scope_document_ids: list[str] = field(default_factory=list)
    resolved_from: str = ""
    dense_hits: list[dict[str, object]] = field(default_factory=list)
    lexical_hits: list[dict[str, object]] = field(default_factory=list)
    selected_chunk_ids: list[str] = field(default_factory=list)
    anchors_applied: list[str] = field(default_factory=list)
    quota_applied: bool = False
    max_score: float = 0.0
    context_chars: int = 0


@dataclass
class RetrievalResult:
    chunks: list[Chunk]
    trace: RetrievalTrace
    intent: Intent
    scope: Scope
    has_context: bool
    low_evidence: bool


def retrieve(
    *, session: Session, message: str, explicit_job_ids: list[str] | None = None
) -> RetrievalResult:
    """Run the full retrieval pipeline for one question."""
    documents = list(Document.objects.for_session(session))
    jobs = [
        JobRef(
            document_id=str(d.id),
            ordinal=d.ordinal,
            label=d.display_label,
            company=d.company,
        )
        for d in documents
        if d.kind == DocumentKind.JOB
    ]

    scope = resolve(message, jobs=jobs, explicit_ids=explicit_job_ids)
    intent = route(message)

    trace = RetrievalTrace(
        query=message,
        intent=intent.value,
        scope_document_ids=scope.document_ids,
        resolved_from=scope.resolved_from,
    )

    if not scope.document_ids:
        return RetrievalResult([], trace, intent, scope, has_context=False, low_evidence=False)

    expanded = _expand(message, session=session, scope=scope, intent=intent)
    trace.expanded_query = expanded

    embedder = get_embedder()
    dense_hits = dense.search(
        session=session,
        query_vector=embedder.embed_query(message),
        document_ids=scope.document_ids,
    )
    lexical_hits = lexical.search(session=session, query=expanded, document_ids=scope.document_ids)

    trace.dense_hits = [
        {"chunk_id": c.chunk_id, "rank": c.rank, "score": round(c.score, 4)}
        for c in dense_hits[:10]
    ]
    trace.lexical_hits = [
        {"chunk_id": c.chunk_id, "rank": c.rank, "score": round(c.score, 4)}
        for c in lexical_hits[:10]
    ]

    fused = fusion.reciprocal_rank_fusion(dense_hits, lexical_hits)
    # Relevance is judged on the dense arm's best cosine score; ordering comes
    # from RRF. See fusion.evaluate_floor.
    top_similarity = max((c.score for c in dense_hits), default=0.0)
    outcome = fusion.evaluate_floor(fused, top_similarity=top_similarity)
    trace.max_score = round(outcome.max_score, 5)

    if not outcome.has_context:
        # Zero tokens spent. A model asked to answer from nothing produces
        # something plausible, which is the worst possible output here.
        log.info("retrieval_below_floor", max_score=outcome.max_score, intent=intent.value)
        return RetrievalResult([], trace, intent, scope, has_context=False, low_evidence=False)

    selected = fusion.apply_quota(fused, document_ids=scope.document_ids)
    trace.quota_applied = len(scope.document_ids) > 1

    anchors = _anchor_chunk_ids(session=session, scope=scope, intent=intent)
    if anchors:
        selected = fusion.apply_anchors(selected, anchor_chunk_ids=anchors, all_fused=fused)
        trace.anchors_applied = anchors

    chunks = _materialize(session=session, selected=selected)
    trace.selected_chunk_ids = [str(c.id) for c in chunks]
    trace.context_chars = sum(len(c.text) for c in chunks)

    return RetrievalResult(
        chunks=chunks,
        trace=trace,
        intent=intent,
        scope=scope,
        has_context=True,
        low_evidence=outcome.low_evidence,
    )


def _expand(message: str, *, session: Session, scope: Scope, intent: Intent) -> str:
    """Widen the lexical query with the in-scope jobs' requirement skills.

    Free, and it works with no API key because the deterministic extractor
    produced those rows at ingest. Without it, "what am I missing?" gives the
    lexical arm almost nothing to match — the user's phrasing rarely contains
    the skill nouns the posting used.
    """
    if not expands_query(intent):
        return message

    skills = (
        Requirement.objects.for_session(session)
        .filter(document_id__in=scope.document_ids)
        .values_list("skill", flat=True)
        .distinct()[:25]
    )
    terms = " ".join(sorted({s for s in skills if s}))
    return f"{message} {terms}".strip() if terms else message


def _anchor_chunk_ids(*, session: Session, scope: Scope, intent: Intent) -> list[str]:
    """Chunk ids for the sections this intent must always see."""
    kinds = anchor_sections(intent)
    if not kinds:
        return []

    section_ids = (
        Section.objects.for_session(session)
        .filter(document_id__in=scope.document_ids, kind__in=[k.value for k in kinds])
        .values_list("id", flat=True)
    )
    if not section_ids:
        return []

    return [
        str(chunk_id)
        for chunk_id in Chunk.objects.for_session(session)
        .filter(section_id__in=list(section_ids), is_boilerplate=False, injection_flag=False)
        .order_by("document_id", "ordinal")
        .values_list("id", flat=True)[: len(scope.document_ids) * 2]
    ]


def _materialize(*, session: Session, selected: list[fusion.Fused]) -> list[Chunk]:
    """Load the selected chunks, preserving fused order and the char budget."""
    ids = [candidate.chunk_id for candidate in selected]
    if not ids:
        return []

    by_id = {
        str(chunk.id): chunk
        for chunk in Chunk.objects.for_session(session)
        .filter(id__in=ids)
        .select_related("document", "section")
    }

    ordered: list[Chunk] = []
    budget = fusion.MAX_CONTEXT_CHARS
    for chunk_id in ids:
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        if len(chunk.text) > budget:
            break
        ordered.append(chunk)
        budget -= len(chunk.text)

    return ordered
