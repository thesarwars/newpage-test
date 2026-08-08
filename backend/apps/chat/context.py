"""Context assembly: what actually goes into the request, and in what order.

Order is chosen for prefix-cache stability. Anything that changes invalidates
everything after it, so the layout is: frozen system → whole résumé (stable for
the session) → history → retrieved JD chunks → the question. The volatile parts
are last on purpose.

    system            frozen, cached
    messages[0]       whole résumé, one document block, cached
    messages[1..n]    prior turns, text only, no document blocks replayed
    messages[-1]      JD chunk blocks, then the question
"""

from __future__ import annotations

import structlog

from apps.chat.models import Message, Mode, Role
from apps.chat.prompts import SYSTEM_PROMPT, role_prompt
from apps.core.models import Session
from apps.documents.chunking.tokenizer import count_tokens
from apps.documents.models import Chunk, Document, DocumentKind
from llm.types import ChatRequest, DocumentBlock

log = structlog.get_logger(__name__)

MAX_HISTORY_TURNS = 6
HISTORY_TOKEN_BUDGET = 4000


def build(
    *,
    session: Session,
    question: str,
    chunks: list[Chunk],
    mode: str = Mode.ANALYSIS,
    effort: str = "medium",
) -> ChatRequest:
    """Assemble one request. `blocks[i]` is what `document_index == i` refers to."""
    blocks: list[DocumentBlock] = []

    resume = _resume(session)
    if resume is not None and resume.normalized_text:
        blocks.append(
            DocumentBlock(
                title="Résumé",
                # Verbatim `normalized_text`. Not stripped, not re-wrapped, not
                # prefixed — `base_offset` is 0 only because this string is
                # byte-identical to the document's own coordinate space.
                text=resume.normalized_text,
                document_id=str(resume.id),
                base_offset=0,
                # Stable for the whole session, and the largest single block in
                # the request. Caching it is most of the cache win.
                cache=True,
            )
        )

    for chunk in chunks:
        blocks.append(
            DocumentBlock(
                title=_chunk_title(chunk),
                text=chunk.text,
                document_id=str(chunk.document_id),
                # The offset contract, cashed in: `normalized_text[char_start:
                # char_end] == text`, so a block-relative citation offset plus
                # `char_start` is a document-relative one. No search, no
                # fuzzy match, no off-by-one.
                base_offset=chunk.char_start,
                chunk_id=str(chunk.id),
                context=_breadcrumb(chunk),
            )
        )

    return ChatRequest(
        system=SYSTEM_PROMPT,
        system_suffix=role_prompt(mode),
        blocks=blocks,
        question=question,
        history=_history(session),
        effort=effort,  # type: ignore[arg-type]
    )


def _resume(session: Session) -> Document | None:
    """The résumé goes in whole, unconditionally — there is no threshold.

    A two-page résumé is 500-1500 words. Chunking it and retrieving 6 of its 14
    chunks is a self-inflicted recall ceiling on the document the user cares
    most about. Including it whole also means `base_offset == 0`, so the
    résumé's citations need no offset arithmetic at all.

    Résumé chunks still exist and are still indexed — requirement matching and
    the Gap Matrix query them. They are simply not part of the *chat* retrieval
    path. The JD side always goes through retrieval, regardless of size.
    """
    resume: Document | None = (
        Document.objects.for_session(session)
        .filter(kind=DocumentKind.RESUME)
        .order_by("created_at")
        .first()
    )
    return resume


def _chunk_title(chunk: Chunk) -> str:
    label = chunk.document.display_label
    heading = (chunk.section.heading if chunk.section else "") or ""
    return f"{label} — {heading}" if heading else label


def _breadcrumb(chunk: Chunk) -> str:
    """The structural prefix already computed at ingest.

    Recovered from `embed_text` rather than rebuilt, so the model sees exactly
    the context the encoder saw. Sent as the block's `context` field, which is
    not citable text and therefore does not shift a single offset.
    """
    if chunk.embed_text.endswith(chunk.text):
        return chunk.embed_text[: -len(chunk.text)].strip()
    return ""


def _history(session: Session) -> list[tuple[str, str]]:
    """Prior turns, newest-first budget, oldest dropped whole.

    Whole messages, never truncated mid-message: half a user question is worse
    than no user question, and a severed assistant turn reads as the model
    having said something it did not finish saying.

    Document blocks are never replayed into history. Only the turn-1 résumé
    block persists, which is both cheaper and the reason the cached prefix
    survives across turns.
    """
    recent = list(
        Message.objects.for_session(session)
        .exclude(content="")
        .order_by("-created_at")[:MAX_HISTORY_TURNS]
    )

    kept: list[tuple[str, str]] = []
    budget = HISTORY_TOKEN_BUDGET
    for message in recent:
        # bge's tokenizer, not Claude's. It is the wrong tokenizer for an exact
        # count and the right one for a budget: it is already loaded, it costs
        # nothing, and it is within ~15% on English prose. An exact count would
        # need a network round-trip on the path optimised for time-to-first-token.
        cost = count_tokens(message.content)
        if cost > budget:
            break
        budget -= cost
        role = Role.ASSISTANT if message.role == Role.ASSISTANT else Role.USER
        kept.append((str(role.value), message.content))

    kept.reverse()
    return _pair(kept)


def _pair(turns: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop leading assistant turns and collapse consecutive same-role turns.

    Trimming by budget can leave history starting with an assistant turn, or
    with two user turns in a row where an assistant turn was dropped between
    them. Both are 400s from the API. Fixing it here rather than at the call
    site means every caller gets a valid conversation, including the fake.
    """
    cleaned: list[tuple[str, str]] = []
    for role, text in turns:
        if not cleaned and role == Role.ASSISTANT:
            continue
        if cleaned and cleaned[-1][0] == role:
            cleaned[-1] = (role, f"{cleaned[-1][1]}\n\n{text}")
            continue
        cleaned.append((role, text))

    # A trailing user turn would collide with the question turn appended by the
    # gateway, producing two user turns in a row.
    if cleaned and cleaned[-1][0] == Role.USER:
        cleaned.pop()
    return cleaned
