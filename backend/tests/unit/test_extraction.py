"""Keyless requirement extraction and matching.

The argument this code exists to prove: **vector search cannot retrieve
absence.** A top-k retriever asked "what am I missing?" returns the chunks most
similar to the question, which are the ones describing what the candidate has.
Gap analysis has to iterate requirements and check each one — a set difference,
not a similarity search.
"""

from __future__ import annotations

from typing import ClassVar

from apps.analysis.extractors.deterministic import DeterministicExtractor
from apps.analysis.matcher import ResumeChunk, match_requirement
from apps.analysis.models import ExtractorSource, MatchStatus, RequirementCategory
from apps.documents.chunking.sections import detect_sections
from apps.documents.normalize import normalize

JOB = normalize("""\
Staff Backend Engineer

REQUIREMENTS
- 6+ years in backend or platform engineering.
- Production Kubernetes experience, including cluster upgrades.
- Terraform in production, managing real infrastructure state.
- Strong Go.
- Deep understanding of CI/CD pipelines.

NICE TO HAVE
- Exposure to SOC 2 compliance work.
- Public speaking or written technical advocacy.

BENEFITS
- Fully remote within the UK.
- Learning budget of GBP 2,000 a year.
""").text


def _extract() -> list:
    return DeterministicExtractor().extract(normalized_text=JOB, sections=detect_sections(JOB))


class TestExtraction:
    def test_core_skills_are_found(self) -> None:
        skills = {r.skill for r in _extract()}

        assert {"kubernetes", "terraform", "go", "ci/cd"} <= skills

    def test_a_short_requirement_is_not_dropped(self) -> None:
        """ "Strong Go." is ten characters and a real must-have.

        A blanket minimum-length filter discarded it silently — the length guard
        now only applies when no known skill was recognised at all.
        """
        assert "go" in {r.skill for r in _extract()}

    def test_nice_to_have_section_is_not_must_have(self) -> None:
        """Counting optional wants as gaps inflates every gap list with things
        the employer explicitly called optional.

        Asserted on the *source text* of each requirement rather than on its
        derived skill name: the fallback namer is a heuristic, and pinning its
        exact output here would make this test fail for reasons that have
        nothing to do with must-have classification.
        """
        extracted = _extract()

        optional = [r for r in extracted if "SOC 2" in r.text or "Public speaking" in r.text]
        required = [r for r in extracted if "Kubernetes" in r.text or "Terraform" in r.text]

        assert optional and required, "fixture should yield both kinds"
        assert all(r.must_have is False for r in optional)
        assert all(r.must_have is True for r in required)

    def test_benefits_are_never_extracted_as_requirements(self) -> None:
        skills = {r.skill for r in _extract()}

        assert not any("remote" in s or "budget" in s for s in skills)

    def test_fallback_skills_are_usable_names(self) -> None:
        """Names are rendered in the Gap Matrix and matched against the résumé,
        so "with service mesh" or "exposure to soc" are not acceptable."""
        for requirement in _extract():
            assert not requirement.skill.startswith(("with ", "of ", "in ", "to ", "and "))
            assert not requirement.skill.endswith((" or", " and", " with"))

    def test_offsets_index_into_normalized_text(self) -> None:
        """Same offset contract as chunks — the UI highlights these spans."""
        for requirement in _extract():
            assert requirement.text in JOB[requirement.char_start : requirement.char_end]

    def test_years_requirements_are_categorised_as_seniority(self) -> None:
        by_text = {r.text: r for r in _extract()}
        years = next(r for text, r in by_text.items() if "6+ years" in text)

        assert years.category == RequirementCategory.SENIORITY

    def test_source_is_recorded(self) -> None:
        assert DeterministicExtractor().source == ExtractorSource.DETERMINISTIC

    def test_a_document_with_no_requirements_section_yields_nothing(self) -> None:
        text = normalize("About Us\nWe are a company that does things.").text

        assert (
            DeterministicExtractor().extract(normalized_text=text, sections=detect_sections(text))
            == []
        )


class TestMatching:
    RESUME: ClassVar[list[ResumeChunk]] = [
        ResumeChunk(
            "c1", "Rebuilt the shipment tracking service in Go, cutting p99 latency.", "experience"
        ),
        ResumeChunk(
            "c2", "Languages: Python, Go, SQL. Infrastructure: Docker, AWS, Terraform.", "skills"
        ),
        ResumeChunk("c3", "BSc Computer Science, University of Bristol", "education"),
    ]

    def _match(self, skill: str):
        from apps.analysis.extractors.base import ExtractedRequirement

        return match_requirement(
            ExtractedRequirement(
                text=f"{skill} experience",
                skill=skill,
                category=RequirementCategory.HARD_SKILL,
                must_have=True,
                char_start=0,
                char_end=1,
            ),
            self.RESUME,
        )

    def test_evidenced_in_experience_is_strong(self) -> None:
        result = self._match("go")

        assert result.status == MatchStatus.STRONG
        assert "c1" in result.evidence_chunk_ids

    def test_listed_only_in_skills_is_partial(self) -> None:
        """ "Listed Terraform under Skills" and "ran Terraform in production" are
        different claims, and a boolean would report both as a match."""
        result = self._match("terraform")

        assert result.status == MatchStatus.PARTIAL
        assert "c2" in result.evidence_chunk_ids

    def test_absent_is_missing(self) -> None:
        result = self._match("kubernetes")

        assert result.status == MatchStatus.MISSING
        assert result.evidence_chunk_ids == []

    def test_aliases_match(self) -> None:
        """The résumé says Terraform; a posting saying "TF" must still match."""
        assert self._match("tf").status == MatchStatus.PARTIAL

    def test_substring_collisions_do_not_produce_false_matches(self) -> None:
        """ "go" must not match inside "Django", "algorithms" or "category".

        A plain `in` check marks a Python-only candidate as matching a Go
        requirement, which is the most damaging class of error here — it tells
        someone they are qualified when they are not.
        """
        resume = [ResumeChunk("c1", "Maintained a Django monolith with algorithms.", "experience")]
        from apps.analysis.extractors.base import ExtractedRequirement

        result = match_requirement(
            ExtractedRequirement(
                text="Strong Go",
                skill="go",
                category=RequirementCategory.HARD_SKILL,
                must_have=True,
                char_start=0,
                char_end=1,
            ),
            resume,
        )

        assert result.status == MatchStatus.MISSING

    def test_empty_resume_reports_missing_not_an_error(self) -> None:
        from apps.analysis.extractors.base import ExtractedRequirement

        result = match_requirement(
            ExtractedRequirement(
                text="Kubernetes",
                skill="kubernetes",
                category=RequirementCategory.HARD_SKILL,
                must_have=True,
                char_start=0,
                char_end=1,
            ),
            [],
        )

        assert result.status == MatchStatus.MISSING
