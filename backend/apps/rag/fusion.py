"""Rank fusion, quotas, anchors and the evidence floor.

Everything between "two rankings" and "the twelve chunks that go to the model".

**RRF over weighted score normalization.** Cosine similarity and `ts_rank_cd`
live on incomparable scales with per-query distributions — normalizing them
means picking weights, and picking weights means tuning them on a golden set
small enough that the tuning is noise. Reciprocal Rank Fusion uses only the
*ordering* from each arm, needs no tuning at all, and is defensible on its own
merits rather than as a shortcut.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.rag.dense import Candidate

# The standard RRF constant. Large enough that rank 1 and rank 2 are not wildly
# far apart, small enough that rank 30 contributes almost nothing.
RRF_K = 60

# Sorts after any real rank when an arm did not return a chunk at all.
_UNRANKED = 10_000

# Chunks that reach the model.
TOP_K = 12
# Hard ceiling on assembled chunk text, independent of count.
MAX_CONTEXT_CHARS = 6000

# Minimum chunks per in-scope job, so a cross-job comparison cannot be answered
# entirely from whichever posting happens to be phrased most like the question.
MIN_PER_DOCUMENT = 3

# The floor is measured on **cosine similarity**, not on the RRF score.
#
# This was originally an RRF threshold and that was wrong in a way the eval
# caught immediately: RRF is a pure rank statistic, so the top chunk scores
# 1/(60+1) = 0.0164 whether it is a perfect match or the least-bad of thirty
# irrelevant ones. It cannot express "nothing here is relevant", which is the
# only question a floor exists to answer. (It also meant the original 0.020
# threshold sat *above* the maximum achievable single-arm score, so every query
# fell through the floor.)
#
# Cosine similarity does carry that meaning: on this corpus a strong match runs
# 0.75-0.85 and an unrelated question lands near 0.3.
RETRIEVAL_FLOOR = 0.35
# Between the two, answer but stamp the response as thin evidence.
LOW_EVIDENCE = 0.50


@dataclass
class Fused:
    chunk_id: str
    document_id: str
    rrf: float
    dense_rank: int | None = None
    lexical_rank: int | None = None
    anchored: bool = False
    quota_filled: bool = False


@dataclass
class RetrievalOutcome:
    selected: list[Fused] = field(default_factory=list)
    max_score: float = 0.0
    has_context: bool = True
    low_evidence: bool = False


def reciprocal_rank_fusion(
    dense: list[Candidate], lexical: list[Candidate], *, k: int = RRF_K
) -> list[Fused]:
    """Fuse two rankings by 1/(k + rank), summed.

    A chunk ranked #1 by one arm and #20 by the other beats a chunk ranked #5 by
    both — which is the intended behaviour: strong evidence from either arm is
    worth more than being mediocre in both.
    """
    scores: dict[str, Fused] = {}

    for candidates, attribute in ((dense, "dense_rank"), (lexical, "lexical_rank")):
        for candidate in candidates:
            entry = scores.get(candidate.chunk_id)
            if entry is None:
                entry = Fused(
                    chunk_id=candidate.chunk_id,
                    document_id=candidate.document_id,
                    rrf=0.0,
                )
                scores[candidate.chunk_id] = entry
            entry.rrf += 1.0 / (k + candidate.rank)
            setattr(entry, attribute, candidate.rank)

    # Tiebreak on the arms' own ranks, not on chunk_id. Ids are UUIDs minted at
    # ingest, so an id tiebreaker reshuffles equal-RRF chunks on every re-ingest
    # — which made the fused MRR drift run to run while both input arms were
    # perfectly stable. Preferring the dense arm's opinion is also the more
    # defensible rule: it is the one that measures meaning rather than tokens.
    return sorted(
        scores.values(),
        key=lambda f: (-f.rrf, f.dense_rank or _UNRANKED, f.lexical_rank or _UNRANKED),
    )


def apply_quota(
    fused: list[Fused],
    *,
    document_ids: list[str],
    top_k: int = TOP_K,
    minimum: int = MIN_PER_DOCUMENT,
) -> list[Fused]:
    """Guarantee every in-scope document a minimum share of the window.

    Without this, "how do I compare across these three roles?" reliably returns
    twelve chunks from one document — the one whose language happens to be
    closest to the question — and the answer silently covers one job while
    claiming to cover three.
    """
    if len(document_ids) < 2:
        return fused[:top_k]

    selected: list[Fused] = []
    per_document: dict[str, int] = dict.fromkeys(document_ids, 0)

    # Reserve each document's quota from its own best candidates first.
    for document_id in document_ids:
        for candidate in fused:
            if candidate.document_id != document_id:
                continue
            if per_document[document_id] >= minimum:
                break
            candidate.quota_filled = True
            selected.append(candidate)
            per_document[document_id] += 1

    # Fill the rest by pure rank.
    chosen = {candidate.chunk_id for candidate in selected}
    for candidate in fused:
        if len(selected) >= top_k:
            break
        if candidate.chunk_id not in chosen:
            selected.append(candidate)
            chosen.add(candidate.chunk_id)

    selected.sort(key=lambda f: -f.rrf)
    return selected[:top_k]


def apply_anchors(
    selected: list[Fused], *, anchor_chunk_ids: list[str], all_fused: list[Fused]
) -> list[Fused]:
    """Force specific chunks into the window regardless of rank.

    Used for the target job's REQUIREMENTS chunk on gap/fit/alignment questions.
    Removes the most embarrassing failure this system can have: reasoning
    confidently about what someone is missing having never retrieved the list of
    what was asked for.
    """
    if not anchor_chunk_ids:
        return selected

    by_id = {candidate.chunk_id: candidate for candidate in all_fused}
    already = {candidate.chunk_id: candidate for candidate in selected}

    missing: list[Fused] = []
    for chunk_id in anchor_chunk_ids:
        if chunk_id in already:
            already[chunk_id].anchored = True
            continue
        # Not selected. Take the fused entry if retrieval saw it at all,
        # otherwise synthesise one — an anchor that applies only when retrieval
        # already found the chunk is not an anchor.
        anchor = by_id.get(chunk_id) or Fused(chunk_id=chunk_id, document_id="", rrf=0.0)
        anchor.anchored = True
        missing.append(anchor)

    if not missing:
        return selected

    # Evict the lowest-ranked *non-anchored* entries to make room, so two
    # anchors cannot displace each other and the window size stays fixed.
    keep = [c for c in selected if not c.anchored]
    anchored = [c for c in selected if c.anchored]
    room = max(0, len(selected) - len(anchored) - len(missing))

    return anchored + missing + keep[:room]


def evaluate_floor(fused: list[Fused], *, top_similarity: float) -> RetrievalOutcome:
    """Decide whether there is enough evidence to answer at all.

    The cheap half of the guardrail story: below the floor no model call happens,
    which costs zero tokens and removes the hallucination surface entirely. A
    model asked to answer from nothing will produce something plausible.

    `top_similarity` is the best cosine score from the dense arm — a semantic
    measure — while `fused` supplies the ordering. Ranking and relevance are
    different questions and this uses the right statistic for each.
    """
    if not fused or top_similarity < RETRIEVAL_FLOOR:
        return RetrievalOutcome(selected=[], max_score=top_similarity, has_context=False)

    return RetrievalOutcome(
        selected=fused,
        max_score=top_similarity,
        has_context=True,
        low_evidence=top_similarity < LOW_EVIDENCE,
    )
