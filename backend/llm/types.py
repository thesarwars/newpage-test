"""The wire between the chat layer and whatever is generating text.

Both backends — the real Anthropic client and the keyless fake — emit this same
event stream. That is the whole point: `apps/chat/streaming.py` never learns
which one it is talking to, so the keyless path exercises the identical citation
mapping, persistence and SSE code the real path does. A fake that produced a
different event shape would let the two drift, and the drift would surface on a
reviewer's machine rather than in CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# thinking + visible text SHARE this cap on opus-5. 4096 intermittently truncates
# a 12-chunk gap answer mid-sentence — while the citation mapper is splicing
# offsets into that sentence. Unused headroom is free: output bills on tokens
# produced, not on the cap.
MAX_TOKENS = 8192


@dataclass(frozen=True)
class DocumentBlock:
    """One `document` content block, and where its text lives in the corpus.

    `base_offset` is the invariant the entire citation feature rests on. The API
    returns `char_location` offsets *relative to the block*; adding
    `base_offset` puts them back into the parent document's `normalized_text`,
    which is the coordinate space the evidence panel addresses spans in.

    For the whole-résumé block that is 0. For a JD chunk it is `chunk.char_start`
    — which is exact only because `normalized_text[char_start:char_end] == text`
    holds (M3). `text` here is that slice verbatim: no trimming, no re-wrapping,
    no prefix. A single stripped leading newline silently shifts every citation
    in the block by one character.
    """

    title: str
    text: str
    document_id: str
    base_offset: int = 0
    chunk_id: str | None = None
    # Breadcrumb ("Resume > Experience > Senior Engineer, Acme"). Sent as the
    # block's `context` field, which is *not* part of the citable text and
    # therefore cannot shift a single offset.
    context: str = ""
    cache: bool = False

    def to_anthropic(self) -> dict[str, Any]:
        block: dict[str, Any] = {
            "type": "document",
            "title": self.title,
            "source": {"type": "text", "media_type": "text/plain", "data": self.text},
            "citations": {"enabled": True},
        }
        if self.context:
            block["context"] = self.context
        if self.cache:
            block["cache_control"] = {"type": "ephemeral"}
        return block


@dataclass(frozen=True)
class ChatRequest:
    """Everything needed to produce one assistant turn."""

    # Cached prefix: the frozen, mode-independent body of the system prompt.
    system: str
    # Uncached tail: the one-paragraph mode line. Kept out of the cached block so
    # that switching between analysis and interview mode mid-session still hits
    # the ~900-token cached prefix instead of paying full input price for it.
    system_suffix: str = ""
    blocks: list[DocumentBlock] = field(default_factory=list)
    question: str = ""
    # (role, text) for prior turns. Document blocks are never replayed — only the
    # turn-1 résumé block persists, deliberately, so the cached prefix survives.
    history: list[tuple[str, str]] = field(default_factory=list)
    model: str = "claude-opus-5"
    effort: Literal["low", "medium", "high"] = "medium"
    max_tokens: int = MAX_TOKENS


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class CitationDelta:
    """A `char_location` citation, already resolved to document coordinates.

    `answer_char` is the length of the assistant text emitted *before* this
    citation arrived — i.e. where the `[n]` marker belongs. It is recorded by the
    backend rather than recomputed later because it is only knowable while the
    stream is in flight.
    """

    document_index: int
    start_char_index: int
    end_char_index: int
    cited_text: str
    answer_char: int


@dataclass(frozen=True)
class Completed:
    stop_reason: str = ""
    refusal_message: str = ""
    usage: Usage = field(default_factory=Usage)
    request_id: str = ""


StreamEvent = TextDelta | CitationDelta | Completed
