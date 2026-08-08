"""Suggestion chips, templated from what the workspace already knows.

A chat box makes the user do the work of knowing what to ask. Someone who has
just uploaded a résumé and three postings does not have a question — they have an
anxiety. These chips convert the retrieval layer's own findings into the four
questions most worth asking, so the first interaction is a click rather than a
blank prompt.

**Zero LLM calls, by construction.** The inputs are `Requirement` and
`RequirementMatch` rows that the deterministic extractor produced at ingest, so
this works identically with and without an API key — which matters, because the
keyless path is the one a reviewer will be in when they first need a question to
ask. Asking a model what to ask a model would also be slower, cost money, and
produce something less specific than the posting's own words.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from apps.analysis.extractors.deterministic import is_curated
from apps.analysis.models import MatchStatus, Requirement, RequirementMatch
from apps.core.models import Session
from apps.documents.models import Document, DocumentKind, rail_order

MAX_CHIPS = 4


@dataclass(frozen=True)
class Suggestion:
    label: str
    """The question that will actually be sent, which may be longer than the label."""
    message: str
    intent: str


def build(
    *, session: Session, job_ids: list[str] | None = None, mode: str = "analysis"
) -> list[Suggestion]:
    """Four chips for this workspace, most specific first.

    Ordering is the whole design. A generic "How do I match this role?" is always
    available and always the least useful thing on screen, so it appears only
    when nothing more specific can be said.
    """
    documents = list(rail_order(Document.objects.for_session(session)))
    resume = next((d for d in documents if d.kind == DocumentKind.RESUME), None)
    jobs = [d for d in documents if d.kind == DocumentKind.JOB]

    if job_ids:
        wanted = set(job_ids)
        jobs = [job for job in jobs if str(job.id) in wanted]

    if not jobs:
        return _empty_workspace(resume is not None)

    if mode == "interview":
        return _interview(jobs)

    return _analysis(session=session, jobs=jobs, has_resume=resume is not None)


def _empty_workspace(has_resume: bool) -> list[Suggestion]:
    """Nothing to ask about yet. Say what would make it possible instead."""
    if has_resume:
        return [
            Suggestion("What's in my résumé?", "What does my résumé say I've done?", "meta"),
        ]
    return []


def _analysis(*, session: Session, jobs: list[Document], has_resume: bool) -> list[Suggestion]:
    chips: list[Suggestion] = []
    job_ids = [job.id for job in jobs]

    # The single most-demanded skill the résumé does not evidence. This is the
    # chip worth having: it names a specific gap rather than inviting the user to
    # go looking for one.
    missing = _top_missing(session=session, job_ids=job_ids) if has_resume else []
    if missing:
        skill = missing[0]
        chips.append(
            Suggestion(
                f"Am I blocked by {skill}?",
                f"The postings ask for {skill} and I don't think my résumé shows it. "
                f"How much does that matter, and what do I have that's closest?",
                "gap",
            )
        )

    if len(jobs) > 1:
        chips.append(
            Suggestion(
                f"Compare all {len(jobs)}",
                "Compare these roles against my background. Which is the strongest "
                "fit, and what separates them?",
                "compare",
            )
        )

    label = jobs[0].display_label
    chips.append(
        Suggestion(
            "What am I missing?",
            f"What does {label} require that my résumé doesn't evidence?",
            "gap",
        )
    )

    if has_resume:
        chips.append(
            Suggestion(
                "Which projects fit?",
                f"Which of my projects is most relevant to {label}, and why?",
                "alignment",
            )
        )

    # Only reached when nothing above applied — a workspace with jobs but no
    # résumé. A generic chip is better than an empty row, but it earns its place
    # last.
    if not chips:
        chips.append(Suggestion("How do I match?", f"How do I match {label}?", "alignment"))

    return chips[:MAX_CHIPS]


def _interview(jobs: list[Document]) -> list[Suggestion]:
    label = jobs[0].display_label
    return [
        Suggestion(
            "Likely questions",
            f"What will they probe hardest on for {label}, given my background?",
            "interview",
        ),
        Suggestion(
            "Where I'm thin",
            f"Which {label} requirement am I least able to answer for, and how do "
            "I address it honestly?",
            "interview",
        ),
        Suggestion(
            "My strongest story",
            f"Which of my projects should I lead with for {label}?",
            "interview",
        ),
    ][:MAX_CHIPS]


def _top_missing(*, session: Session, job_ids: Sequence[object]) -> list[str]:
    """Skills demanded by the in-scope postings that the résumé doesn't evidence.

    Ordered by how many postings want them — a gap that three employers care
    about is a better use of the user's evening than one that appears once.
    """
    unmatched = (
        RequirementMatch.objects.for_session(session)
        .filter(
            requirement__document_id__in=job_ids,
            status=MatchStatus.MISSING,
            requirement__must_have=True,
        )
        .values_list("requirement__skill", flat=True)
    )

    counts: dict[str, int] = {}
    for skill in unmatched:
        if skill:
            counts[skill] = counts.get(skill, 0) + 1

    # Ordering: most-demanded first, then curated names ahead of guessed ones,
    # then alphabetical.
    #
    # The middle term is the one that matters. With a plain alphabetical
    # tie-break the top chip came out "Am I blocked by backend services?" — a
    # phrase the fallback extractor lifted out of "4+ years building production
    # backend services", which does not appear verbatim in the résumé and is
    # therefore reported missing for a backend engineer. `kubernetes` was sitting
    # behind it on the same count. A curated skill is a named technology; a
    # fallback one is good enough to index on and not good enough to put in a
    # sentence addressed to the user.
    #
    # Alphabetical last, so the row is stable between reloads — a suggestion that
    # moves when nothing changed reads as a rendering bug.
    return [
        skill
        for skill, _ in sorted(
            counts.items(), key=lambda kv: (-kv[1], not is_curated(kv[0]), kv[0])
        )
    ]


def known_skills(*, session: Session, job_ids: Sequence[object]) -> list[str]:
    """Every skill the in-scope postings ask for. Used by the empty-state copy."""
    return sorted(
        {
            skill
            for skill in Requirement.objects.for_session(session)
            .filter(document_id__in=job_ids)
            .values_list("skill", flat=True)
            if skill
        }
    )
