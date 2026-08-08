"""Lexical retrieval over Postgres full-text search.

The other arm. It exists because job descriptions are dense with exact tokens a
384-dimension embedder blurs: `Kubernetes` against `Docker`, `Terraform`,
`gRPC`, `dbt`, `SOC 2`. Dense retrieval catches "led a team" ↔ "people
management"; lexical catches `k8s`. Neither alone is enough.

Same table, same query plan, same transaction as the dense arm — which is most
of why pgvector was chosen over a separate vector database. Rank fusion across
two datastores would mean two round trips and no way to filter both consistently.
"""

from __future__ import annotations

import functools
import operator
import re

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F

from apps.core.models import Session
from apps.documents.models import Chunk
from apps.rag.dense import CANDIDATE_LIMIT, Candidate

# Words that carry no retrieval signal in a question. Removed before building
# the query so they neither dilute the ranking nor waste an OR term.
_STOPWORDS = frozenset(
    {
        "what",
        "which",
        "who",
        "how",
        "why",
        "when",
        "where",
        "am",
        "is",
        "are",
        "do",
        "does",
        "did",
        "the",
        "a",
        "an",
        "for",
        "of",
        "to",
        "in",
        "on",
        "my",
        "me",
        "i",
        "you",
        "this",
        "that",
        "these",
        "those",
        "and",
        "or",
        "have",
        "has",
        "had",
        "be",
        "been",
        "should",
        "would",
        "could",
        "can",
        "job",
        "role",
        "about",
        "with",
        "from",
        "at",
        "it",
        "not",
        "any",
    }
)
_TOKEN = re.compile(r"[A-Za-z0-9+#./-]{2,}")


def _or_query(text: str) -> SearchQuery | None:
    """Build an OR-of-terms tsquery.

    **Not `websearch_to_tsquery`.** That ANDs unquoted terms, which is right for
    a search box and catastrophic here: query expansion appends ~25 skill nouns
    from the job's requirements, and no chunk on earth contains all of them, so
    the whole lexical arm returned nothing. The eval caught it as a flat 0.000
    hit-rate — expansion had made the arm strictly worse than no arm at all.

    OR is the correct semantics for this application anyway. The lexical arm's
    job is to catch *any* exact technical token the embedder blurred; requiring
    all of them defeats the purpose.
    """
    terms = [token.lower() for token in _TOKEN.findall(text) if token.lower() not in _STOPWORDS]
    # Deduplicate, preserving order, so ranking is stable across identical
    # queries with repeated words.
    unique = list(dict.fromkeys(terms))[:40]
    if not unique:
        return None

    return functools.reduce(operator.or_, (SearchQuery(term, config="english") for term in unique))


def search(
    *,
    session: Session,
    query: str,
    document_ids: list[str],
    limit: int = CANDIDATE_LIMIT,
) -> list[Candidate]:
    """Top chunks by ts_rank_cd, scoped identically to the dense arm."""
    if not document_ids or not query.strip():
        return []

    search_query = _or_query(query)
    if search_query is None:
        return []

    rows = list(
        Chunk.objects.for_session(session)
        .filter(document_id__in=document_ids)
        .filter(is_boilerplate=False, injection_flag=False)
        .filter(search_vector=search_query)
        # cover_density weights proximity of matched terms, which suits
        # requirement bullets where the useful signal is several terms close
        # together rather than one term repeated.
        .annotate(rank=SearchRank(F("search_vector"), search_query, cover_density=True))
        # ts_rank ties are common: many chunks match one OR term equally.
        # Tiebreak on the corpus's own stable ordering, not on `id`. The
        # eval re-ingests the fixtures each run, so every UUID is fresh and
        # an id tiebreaker is a *random* tiebreaker — which is exactly what
        # made hit-rate wobble between 0.958 and 1.000 run to run.
        .order_by("-rank", "document__kind", "document__ordinal", "ordinal")[:limit]
    )

    return [
        Candidate(
            chunk_id=str(chunk.id),
            document_id=str(chunk.document_id),
            score=float(chunk.rank),
            rank=index + 1,
        )
        for index, chunk in enumerate(rows)
    ]
