"""Real token counting, using the embedding model's own tokenizer.

Not a character proxy, and not tiktoken. Both are wrong here, in a way that
produces no error:

* A 4-chars-per-token proxy under-counts dense technical text by roughly 25%.
  A chunk budgeted at "512 tokens" arrives as ~680 real tokens, bge-small
  silently truncates everything past its 512-token limit, and the tail of that
  chunk becomes **invisible to the index**. Retrieval quietly gets worse and
  nothing anywhere reports a problem.
* tiktoken is OpenAI's tokenizer. Counting bge-small's budget with it is
  measuring one thing to constrain another.

So the chunker asks the actual model. The tokenizer ships inside the same
fastembed model directory that is baked into the image, which means no network
call and no second artifact to keep in sync with the encoder.
"""

from __future__ import annotations

import functools
from pathlib import Path

from tokenizers import Tokenizer

# bge-small-en-v1.5's hard sequence limit. Text beyond this is dropped by the
# encoder without warning, which is why the chunker asserts against it.
MAX_SEQUENCE_TOKENS = 512

# The encoder wraps every input in [CLS] … [SEP].
SPECIAL_TOKEN_OVERHEAD = 2


@functools.lru_cache(maxsize=1)
def _tokenizer() -> Tokenizer:
    """Load the tokenizer that belongs to the configured embedding model.

    Cached: loading is tens of milliseconds and the chunker calls this per
    candidate span.
    """
    return Tokenizer.from_file(str(_tokenizer_path()))


def _tokenizer_path() -> Path:
    """Find `tokenizer.json` inside the baked fastembed model cache.

    Searched rather than hard-coded: fastembed's on-disk layout includes a
    snapshot hash that changes when the model is re-fetched, and a hard-coded
    path would break on a rebuild with an error pointing at the wrong thing.
    """
    from django.conf import settings

    cache_root = Path(getattr(settings, "FASTEMBED_CACHE_PATH", "/opt/models"))
    candidates = sorted(cache_root.rglob("tokenizer.json"))
    if not candidates:
        raise RuntimeError(
            f"No tokenizer.json under {cache_root}. The embedding model has not been "
            "fetched. In the container it is baked at image build — rebuild with "
            "`make build`. Outside it (CI, or a bare checkout) fetch it first:\n"
            '  python -c "from fastembed import TextEmbedding; '
            "TextEmbedding('BAAI/bge-small-en-v1.5')\""
        )
    return candidates[0]


def count_tokens(text: str) -> int:
    """Token count as the *encoder* will see it, special tokens included."""
    if not text:
        return 0
    return len(_tokenizer().encode(text, add_special_tokens=True).ids)


def fits_in_encoder(text: str) -> bool:
    return count_tokens(text) <= MAX_SEQUENCE_TOKENS


def truncate_to_tokens(text: str, limit: int) -> str:
    """Cut `text` to at most `limit` tokens, on a token boundary.

    A safety net, not a design tool: the splitter is supposed to produce spans
    that already fit. It exists so that a pathological input (one 900-token
    "word", a table rendered without spaces) degrades to a short chunk rather
    than a silently truncated one.
    """
    encoding = _tokenizer().encode(text, add_special_tokens=False)
    if len(encoding.ids) + SPECIAL_TOKEN_OVERHEAD <= limit:
        return text

    keep = max(0, limit - SPECIAL_TOKEN_OVERHEAD)
    if not keep:
        return ""

    # offsets map tokens back to character positions in the original string, so
    # the cut lands on a real boundary rather than mid-codepoint.
    end_char = encoding.offsets[keep - 1][1]
    return text[:end_char]
