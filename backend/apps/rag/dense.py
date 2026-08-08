"""Dense retrieval over pgvector.

One arm of hybrid retrieval. The tenancy and boilerplate filters are part of the
same query rather than applied afterwards: an ANN index that finds another
tenant's neighbours and then discards them has already read the wrong rows, and
at a low `k` it returns fewer good ones than it should.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import connection, transaction
from pgvector.django import CosineDistance

from apps.core.models import Session
from apps.documents.models import Chunk

# Candidates per arm before fusion. Wider than the 12 that reach the model, on
# purpose: RRF needs enough of each ranking to disagree usefully.
CANDIDATE_LIMIT = 30

# HNSW search breadth. Reasoned, not measured — at a few thousand chunks the
# index is barely earning its keep, and the honest place to tune this is a
# recall/latency sweep at a corpus size this project does not have.
EF_SEARCH = 64


@dataclass(frozen=True)
class Candidate:
    chunk_id: str
    document_id: str
    score: float
    rank: int


def search(
    *,
    session: Session,
    query_vector: list[float],
    document_ids: list[str],
    limit: int = CANDIDATE_LIMIT,
) -> list[Candidate]:
    """Nearest chunks by cosine distance, scoped to one session and document set."""
    if not document_ids:
        return []

    queryset = (
        Chunk.objects.for_session(session)
        .filter(document_id__in=document_ids)
        .exclude(embedding=None)
        .filter(is_boilerplate=False, injection_flag=False)
        .annotate(distance=CosineDistance("embedding", query_vector))
        # Tiebreak on the corpus's own stable ordering, not on `id`. The
        # eval re-ingests the fixtures each run, so every UUID is fresh and
        # an id tiebreaker is a *random* tiebreaker — which is exactly what
        # made hit-rate wobble between 0.958 and 1.000 run to run.
        .order_by("distance", "document__kind", "document__ordinal", "ordinal")[:limit]
    )

    # `SET LOCAL` only applies inside a transaction. Under Django's default
    # autocommit it emits a warning and silently does nothing, so `ef_search`
    # would stay at the server default and this tuning would be decorative.
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL hnsw.ef_search = %s", [EF_SEARCH])
        rows = list(queryset)

    return [
        Candidate(
            chunk_id=str(chunk.id),
            document_id=str(chunk.document_id),
            # Cosine similarity, so higher is better and the two arms agree on
            # direction before fusion.
            score=1.0 - float(chunk.distance),
            rank=index + 1,
        )
        for index, chunk in enumerate(rows)
    ]
