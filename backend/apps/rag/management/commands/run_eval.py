"""`manage.py run_eval` — the retrieval evaluation harness.

Runs the golden set against a freshly ingested demo corpus and reports:

* **hit-rate@12** and **MRR@12**, three ways — dense only, lexical only, and
  RRF-fused. That ablation is the deliverable: it is the evidence that hybrid
  retrieval was *measured* rather than assumed, and it means the lexical arm
  either earns its GIN index or gets deleted with a number attached.
* **Routing accuracy** — did "Job #2" resolve to the right document?
* **Gap-F1** — the extracted missing-skill set against hand labels, compared to
  a naive top-k baseline. This is what quantifies "vector search cannot retrieve
  absence".

No API key required. Every metric here is deterministic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.analysis.models import MatchStatus, RequirementMatch
from apps.core.models import Session
from apps.documents.ingest import ingest_upload
from apps.documents.models import Chunk, Document
from apps.rag import dense, fusion, lexical
from apps.rag.embeddings import get_embedder
from apps.rag.pipeline import retrieve
from apps.rag.router import Intent, route
from evals.tagger import document_key, tag

EVALS_DIR = Path(__file__).resolve().parents[4] / "evals"
FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "demo"
# Beside the golden set, not under docs/: the api container mounts only
# backend/, and mounting more of the repo into the app image just so a
# management command can write one file is the wrong trade.
BASELINE = EVALS_DIR / "baseline.json"

CORPUS = [
    ("resume", "resume.pdf"),
    ("job", "job_1_northwind.pdf"),
    ("job", "job_2_vertex.pdf"),
    ("job", "job_3_helio.pdf"),
]


@dataclass
class ArmResult:
    name: str
    hits: int = 0
    total: int = 0
    reciprocal_ranks: list[float] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0

    @property
    def mrr(self) -> float:
        return sum(self.reciprocal_ranks) / self.total if self.total else 0.0


class Command(BaseCommand):
    help = "Evaluate retrieval against the golden set. No API key required."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--write-baseline", action="store_true")
        parser.add_argument("--fail-under-hit-rate", type=float, default=0.0)
        parser.add_argument("--fail-under-routing", type=float, default=0.0)

    def handle(self, *args: Any, **options: Any) -> None:
        golden = json.loads((EVALS_DIR / "golden.json").read_text())
        items = golden["items"]

        session = self._ingest_corpus()
        try:
            report = self._evaluate(session, items)
        finally:
            # The eval ingests a full corpus. Deleting the session cascades to
            # its documents, chunks, requirements and matches, so running the
            # eval never leaves rows behind for the next run to trip over.
            session.delete()

        self._print(report)

        if options["write_baseline"]:
            BASELINE.parent.mkdir(parents=True, exist_ok=True)
            BASELINE.write_text(json.dumps(report, indent=2) + "\n")
            self.stdout.write(self.style.SUCCESS(f"\nbaseline written to {BASELINE}"))

        self._enforce_gates(report, options)

    def _evaluate(self, session: Session, items: list[dict[str, Any]]) -> dict[str, Any]:
        documents = {document_key(d): d for d in Document.objects.for_session(session)}
        by_id = {str(d.id): d for d in documents.values()}

        arms = {
            "dense": ArmResult("dense"),
            "lexical": ArmResult("lexical"),
            "fused": ArmResult("fused"),
        }
        routing_correct = 0
        routing_total = 0
        refusal_correct = 0
        refusal_total = 0

        for item in items:
            if item.get("expect_refusal"):
                refusal_total += 1
                if route(item["question"]) == Intent.OUT_OF_SCOPE:
                    refusal_correct += 1
                continue

            result = retrieve(session=session, message=item["question"])

            if expected := item.get("expect_scope"):
                routing_total += 1
                actual = {
                    document_key(by_id[document_id])
                    for document_id in result.scope.document_ids
                    if document_id in by_id
                }
                if item.get("requires_all_documents"):
                    ok = actual == set(expected)
                else:
                    ok = set(expected).issubset(actual)
                routing_correct += int(ok)

            required = item.get("must_retrieve")
            if not required:
                continue

            self._score_arms(
                arms=arms,
                session=session,
                item=item,
                required=set(required),
                result=result,
            )

        gap = self._score_gap(session=session, items=items, documents=documents)

        return {
            "retrieval": {
                name: {
                    "hit_rate_at_12": round(arm.hit_rate, 4),
                    "mrr_at_12": round(arm.mrr, 4),
                    "items": arm.total,
                }
                for name, arm in arms.items()
            },
            "routing_accuracy": round(routing_correct / routing_total, 4) if routing_total else 0.0,
            "refusal_precision": round(refusal_correct / refusal_total, 4)
            if refusal_total
            else 0.0,
            "gap": gap,
            "golden_items": len(items),
        }

    # ── corpus ────────────────────────────────────────────────────────────
    def _ingest_corpus(self) -> Session:
        """Ingest the demo corpus into a throwaway session."""
        session = Session.objects.create()
        for kind, filename in CORPUS:
            path = FIXTURES / filename
            with path.open("rb") as handle:
                ingest_upload(
                    session=session,
                    kind=kind,
                    filename=filename,
                    size_bytes=path.stat().st_size,
                    handle=handle,
                )
        return session

    # ── retrieval ─────────────────────────────────────────────────────────
    def _score_arms(
        self,
        *,
        arms: dict[str, ArmResult],
        session: Session,
        item: dict[str, Any],
        required: set[str],
        result: Any,
    ) -> None:
        """Score each arm independently, plus the fusion, on the same query.

        The arms are re-run rather than reusing the pipeline's internals so the
        ablation compares like with like: same query, same scope, same filters,
        only the ranking strategy differs.
        """
        document_ids = result.scope.document_ids
        if not document_ids:
            return

        embedder = get_embedder()
        dense_hits = dense.search(
            session=session,
            query_vector=embedder.embed_query(item["question"]),
            document_ids=document_ids,
        )
        lexical_hits = lexical.search(
            session=session,
            query=result.trace.expanded_query or item["question"],
            document_ids=document_ids,
        )
        fused = fusion.reciprocal_rank_fusion(dense_hits, lexical_hits)

        rankings = {
            "dense": [c.chunk_id for c in dense_hits],
            "lexical": [c.chunk_id for c in lexical_hits],
            "fused": [c.chunk_id for c in fused],
        }

        tags = self._tag_lookup(session, {cid for ids in rankings.values() for cid in ids})

        for name, chunk_ids in rankings.items():
            arm = arms[name]
            arm.total += 1
            rank = next(
                (
                    index + 1
                    for index, chunk_id in enumerate(chunk_ids[: fusion.TOP_K])
                    if tags.get(chunk_id) in required
                ),
                None,
            )
            if rank:
                arm.hits += 1
                arm.reciprocal_ranks.append(1.0 / rank)

    @staticmethod
    def _tag_lookup(session: Session, chunk_ids: set[str]) -> dict[str, str]:
        chunks = (
            Chunk.objects.for_session(session)
            .filter(id__in=list(chunk_ids))
            .select_related("document", "section")
        )
        return {str(chunk.id): tag(chunk) for chunk in chunks}

    # ── gap analysis ──────────────────────────────────────────────────────
    def _score_gap(
        self, *, session: Session, items: list[dict[str, Any]], documents: dict[str, Document]
    ) -> dict[str, Any]:
        """Gap-F1 for the structured path against a naive top-k baseline.

        The naive baseline asks the retriever for the chunks most similar to
        "what am I missing?" and reads skills out of them. It scores badly by
        construction — the most similar chunks are the ones describing what the
        candidate *has* — which is the point being demonstrated.
        """
        labelled = [i for i in items if i.get("expected_missing")]
        if not labelled:
            return {"labelled_items": 0}

        structured: list[tuple[float, float, float]] = []
        naive_f1: list[float] = []

        for item in labelled:
            expected = {s.lower() for s in item["expected_missing"]}
            document = documents.get(item["expect_scope"][0])
            if document is None:
                continue

            missing = {
                match.requirement.skill.lower()
                for match in RequirementMatch.objects.for_session(session)
                .filter(
                    requirement__document=document,
                    requirement__must_have=True,
                    status=MatchStatus.MISSING,
                )
                .select_related("requirement")
            }
            structured.append(_prf(expected, missing))

            naive_f1.append(_f1(expected, self._naive_gap(session, item["question"])))

        if not structured:
            return {"labelled_items": 0}

        return {
            "labelled_items": len(structured),
            "structured_precision": round(sum(p for p, _, _ in structured) / len(structured), 4),
            "structured_recall": round(sum(r for _, r, _ in structured) / len(structured), 4),
            "structured_f1": round(sum(f for _, _, f in structured) / len(structured), 4),
            "naive_topk_f1": round(sum(naive_f1) / len(naive_f1), 4) if naive_f1 else 0.0,
        }

    @staticmethod
    def _naive_gap(session: Session, question: str) -> set[str]:
        """What a top-k retriever alone would report as the gap.

        Every known skill mentioned in the retrieved chunks. This scores badly
        by construction, and that is the finding: the chunks most *similar* to
        "what am I missing?" are the ones listing what the job wants and what
        the candidate has, with no signal about which is absent.

        An earlier version of this intersected the prediction with the expected
        set, which made false positives impossible and handed the baseline a
        perfect 1.000 — a comparison that flattered the thing it was meant to
        justify.
        """
        from apps.analysis.extractors.deterministic import _KNOWN_SKILL_SET

        result = retrieve(session=session, message=question)
        text = " ".join(chunk.text for chunk in result.chunks).lower()
        return {
            skill
            for skill in _KNOWN_SKILL_SET
            if re.search(rf"(?<!\w){re.escape(skill)}(?!\w)", text)
        }

    # ── output ────────────────────────────────────────────────────────────
    def _print(self, report: dict[str, Any]) -> None:
        out = self.stdout
        out.write("")
        out.write(self.style.MIGRATE_HEADING("Retrieval ablation (golden set)"))
        out.write(f"  {'arm':<10}{'hit-rate@12':>14}{'MRR@12':>10}{'items':>8}")
        for name in ("dense", "lexical", "fused"):
            arm = report["retrieval"][name]
            out.write(
                f"  {name:<10}{arm['hit_rate_at_12']:>14.3f}{arm['mrr_at_12']:>10.3f}{arm['items']:>8}"
            )

        out.write("")
        out.write(self.style.MIGRATE_HEADING("Routing and refusal"))
        out.write(f"  scope resolution accuracy   {report['routing_accuracy']:.3f}")
        out.write(f"  out-of-scope precision      {report['refusal_precision']:.3f}")

        gap = report["gap"]
        if gap.get("labelled_items"):
            out.write("")
            out.write(self.style.MIGRATE_HEADING("Gap analysis (F1 vs hand labels)"))
            out.write(f"  naive top-k retrieval  F1   {gap['naive_topk_f1']:.3f}")
            out.write(
                f"  structured requirements F1  {gap['structured_f1']:.3f}"
                f"   (P {gap['structured_precision']:.3f} / R {gap['structured_recall']:.3f})"
            )
            out.write("  (the delta is why gap analysis iterates requirements")
            out.write("   rather than asking a retriever what is absent)")
        out.write("")

    def _enforce_gates(self, report: dict[str, Any], options: dict[str, Any]) -> None:
        failures: list[str] = []

        floor = options["fail_under_hit_rate"]
        actual = report["retrieval"]["fused"]["hit_rate_at_12"]
        if floor and actual < floor:
            failures.append(f"fused hit-rate@12 {actual:.3f} < {floor:.3f}")

        routing_floor = options["fail_under_routing"]
        if routing_floor and report["routing_accuracy"] < routing_floor:
            failures.append(
                f"routing accuracy {report['routing_accuracy']:.3f} < {routing_floor:.3f}"
            )

        if failures:
            self.stderr.write(self.style.ERROR("EVAL GATE FAILED"))
            for failure in failures:
                self.stderr.write(self.style.ERROR(f"  {failure}"))
            raise SystemExit(1)


def _prf(expected: set[str], predicted: set[str]) -> tuple[float, float, float]:
    """Precision, recall and F1.

    Reported separately because the hand labels are deliberately non-exhaustive
    — they name the obvious gaps, not every one — so precision is penalised for
    finding *additional* real gaps. A bare F1 would read as "the extractor is
    mediocre" when what it actually shows is high recall against partial labels.
    """
    if not expected:
        return (1.0, 1.0, 1.0) if not predicted else (0.0, 1.0, 0.0)
    if not predicted:
        return (0.0, 0.0, 0.0)

    true_positives = len(expected & predicted)
    if not true_positives:
        return (0.0, 0.0, 0.0)

    precision = true_positives / len(predicted)
    recall = true_positives / len(expected)
    return precision, recall, 2 * precision * recall / (precision + recall)


def _f1(expected: set[str], predicted: set[str]) -> float:
    return _prf(expected, predicted)[2]
