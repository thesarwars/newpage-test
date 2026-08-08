"""The demo corpus, loaded into a session in one call.

This is the highest-leverage endpoint in the project and it is worth saying why.
Every reviewer who opens this app is one step away from an empty screen: they
must find a résumé, find three job postings that are actually related to it, and
upload four files before anything interesting happens. Most will not. "Load demo
data" turns that into one click, and what they see afterwards is a populated
workspace with real retrieval running over it.

It uses the *same* ingest path as an upload — same validators, same parsers, same
chunker, same embedder. Nothing is precomputed and nothing is faked, so the demo
demonstrates the real system rather than a fixture of what the real system would
have produced. It is also why the demo corpus doubles as the eval corpus.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from django.db import transaction

from apps.core.models import Session
from apps.documents.ingest import ingest_upload
from apps.documents.models import Document, DocumentKind, rail_order

log = structlog.get_logger(__name__)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "demo"

# One résumé and three postings, deliberately spread across the fit range: a
# close match, a partial one, and one the candidate is plainly not right for.
# A demo where every score is green demonstrates nothing.
CORPUS: tuple[tuple[str, str, str], ...] = (
    (DocumentKind.RESUME, "resume.pdf", ""),
    (DocumentKind.JOB, "job_1_northwind.pdf", "Senior Backend Engineer — Northwind"),
    (DocumentKind.JOB, "job_2_vertex.pdf", "Platform Engineer — Vertex"),
    (DocumentKind.JOB, "job_3_helio.pdf", "Staff Engineer — Helio"),
)


class WorkspaceNotEmptyError(Exception):
    """Something is already uploaded.

    Refusing is friendlier than merging. The session allows one résumé, so
    seeding on top of a user's own upload would fail deep inside the quota check
    with an error about résumé counts — which is true, and tells the user
    nothing about what they actually did.
    """


def is_seedable(session: Session) -> bool:
    return not Document.objects.for_session(session).exists()


@transaction.atomic
def seed(session: Session) -> list[Document]:
    """Ingest the demo corpus into `session`.

    Idempotent: seeding an already-seeded session returns the existing documents
    rather than duplicating them. A double-click, a retry, or React's
    development-mode double effect must not produce two workspaces' worth of
    documents.

    Atomic: four documents either all land or none do. A half-seeded workspace —
    a résumé and one posting, because the third PDF failed to parse — is worse
    than an empty one, because it looks deliberate.
    """
    if session.demo_seeded:
        return list(rail_order(Document.objects.for_session(session).prefetch_related("sections")))

    if not is_seedable(session):
        raise WorkspaceNotEmptyError()

    documents: list[Document] = []
    for kind, filename, label in CORPUS:
        path = FIXTURES / filename
        with path.open("rb") as handle:
            result = ingest_upload(
                session=session,
                kind=kind,
                filename=filename,
                size_bytes=path.stat().st_size,
                handle=handle,
                label=label,
            )
        documents.append(result.document)

    session.demo_seeded = True
    session.save(update_fields=["demo_seeded", "updated_at"])

    log.info(
        "demo_seeded",
        session_id=str(session.id),
        documents=len(documents),
        chunks=sum(d.chunks.count() for d in documents),
    )
    return documents
