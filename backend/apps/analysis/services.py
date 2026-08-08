"""Turning extractor output into rows, and matching it against the résumé.

Kept out of `ingest.py` so the documents app does not import the analysis app's
internals — ingest calls one function and gets rows.
"""

from __future__ import annotations

import structlog

from apps.analysis.extractors.base import RequirementExtractor
from apps.analysis.extractors.deterministic import DeterministicExtractor
from apps.analysis.matcher import ResumeChunk, match_requirement
from apps.analysis.models import Requirement, RequirementCategory, RequirementMatch
from apps.documents.chunking.sections import Section as DetectedSection
from apps.documents.models import Chunk, Document, DocumentKind

log = structlog.get_logger(__name__)


def get_extractor() -> RequirementExtractor:
    """The deterministic extractor is the default and always available.

    M8 swaps in the LLM implementation when a key is present; the protocol is
    what makes that an upgrade rather than a prerequisite.
    """
    return DeterministicExtractor()


def extract_requirements(
    *,
    document: Document,
    normalized_text: str,
    detected_sections: list[DetectedSection],
) -> list[Requirement]:
    """Extract and persist a job description's requirements."""
    extractor = get_extractor()
    extracted = extractor.extract(normalized_text=normalized_text, sections=detected_sections)

    rows = Requirement.objects.bulk_create(
        Requirement(
            session=document.session,
            document=document,
            text=item.text[:2000],
            skill=item.skill[:120],
            category=item.category,
            must_have=item.must_have,
            evidence_char_start=item.char_start,
            evidence_char_end=item.char_end,
            order=index,
            source=extractor.source,
        )
        for index, item in enumerate(extracted)
    )

    matches = refresh_matches(document=document)

    log.info(
        "requirements_extracted",
        document_id=str(document.id),
        source=extractor.source,
        requirements=len(rows),
        matches=len(matches),
    )
    return list(rows)


def refresh_matches(*, document: Document) -> list[RequirementMatch]:
    """Re-match a job's requirements against the session's résumé.

    Called at job ingest and again when a résumé arrives, because a job uploaded
    before the résumé has requirements but nothing to match them against.
    """
    resume = Document.objects.for_session(document.session).filter(kind=DocumentKind.RESUME).first()
    if resume is None:
        return []

    resume_chunks = [
        ResumeChunk(
            chunk_id=str(chunk.id),
            text=chunk.text,
            section_kind=chunk.section.kind if chunk.section else "other",
        )
        for chunk in Chunk.objects.for_session(document.session)
        .filter(document=resume)
        .select_related("section")
    ]

    requirements = list(Requirement.objects.for_session(document.session).filter(document=document))
    if not requirements or not resume_chunks:
        return []

    RequirementMatch.objects.filter(requirement__in=requirements).delete()

    created: list[RequirementMatch] = []
    evidence_map: dict[str, list[str]] = {}

    for requirement in requirements:
        from apps.analysis.extractors.base import ExtractedRequirement

        result = match_requirement(
            ExtractedRequirement(
                text=requirement.text,
                skill=requirement.skill,
                category=RequirementCategory(requirement.category),
                must_have=requirement.must_have,
                char_start=requirement.evidence_char_start,
                char_end=requirement.evidence_char_end,
            ),
            resume_chunks,
        )
        match = RequirementMatch(
            session=document.session,
            requirement=requirement,
            resume_document=resume,
            status=result.status,
            rationale=result.rationale[:280],
            confidence=result.confidence,
            source=result.source,
        )
        created.append(match)
        evidence_map[str(requirement.id)] = result.evidence_chunk_ids

    RequirementMatch.objects.bulk_create(created)

    # M2M has to be attached after the rows exist. Chunks are fetched rather
    # than passed as raw ids so a stale id from the matcher fails here, loudly,
    # instead of silently attaching nothing.
    chunks_by_id = {
        str(chunk.id): chunk
        for chunk in Chunk.objects.for_session(document.session).filter(
            id__in=[cid for ids in evidence_map.values() for cid in ids]
        )
    }
    for match in created:
        chunks = [
            chunks_by_id[cid]
            for cid in evidence_map.get(str(match.requirement_id), [])
            if cid in chunks_by_id
        ]
        if chunks:
            match.evidence_chunks.set(chunks)

    return created


def refresh_all_jobs(*, resume: Document) -> None:
    """Re-match every job after a résumé lands.

    Upload order is the user's choice, not ours: someone who adds three postings
    and then their CV must not end up with three empty Fit cards.
    """
    for job in Document.objects.for_session(resume.session).filter(kind=DocumentKind.JOB):
        refresh_matches(document=job)
