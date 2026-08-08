"""Section detection and boilerplate flagging."""

from __future__ import annotations

from itertools import pairwise

from apps.documents.chunking.sections import (
    SectionKind,
    classify_heading,
    detect_sections,
    looks_like_heading,
)
from apps.documents.normalize import normalize

RESUME = normalize("""\
ALEX MORAN
Backend Engineer

EXPERIENCE

Senior Backend Engineer, Meridian Logistics — 2022 to Present
- Rebuilt the shipment tracking service in Go.

EDUCATION
BSc Computer Science, University of Bristol — 2019

SKILLS
Python, Go, SQL, PostgreSQL
""").text

JOB = normalize("""\
Staff Backend Engineer

ABOUT THE ROLE
Vertex builds infrastructure tooling.

REQUIREMENTS
- Production Kubernetes experience.
- Terraform in production.

BENEFITS
- Fully remote within the UK.
- Learning budget.

EQUAL OPPORTUNITY
Vertex is committed to equal employment opportunity regardless of race.
""").text


class TestHeadingShape:
    def test_all_caps_short_line_is_a_heading(self) -> None:
        assert looks_like_heading("EXPERIENCE")

    def test_title_case_short_line_is_a_heading(self) -> None:
        assert looks_like_heading("Technical Skills")

    def test_trailing_colon_is_a_heading(self) -> None:
        assert looks_like_heading("Requirements:")

    def test_a_sentence_containing_a_heading_word_is_not_a_heading(self) -> None:
        """Vocabulary alone would misfire here — the shape rule is what saves it."""
        assert not looks_like_heading("I have five years of experience in Python.")

    def test_a_bullet_is_not_a_heading(self) -> None:
        assert not looks_like_heading("- Experience with Kubernetes")

    def test_a_long_line_is_not_a_heading(self) -> None:
        assert not looks_like_heading("EXPERIENCE " * 10)

    def test_blank_is_not_a_heading(self) -> None:
        assert not looks_like_heading("   ")


class TestClassification:
    def test_requirements_synonyms(self) -> None:
        for heading in ("REQUIREMENTS", "Qualifications", "Must-haves", "Who You Are"):
            assert classify_heading(heading) == SectionKind.REQUIREMENTS

    def test_nice_to_have_wins_over_requirements(self) -> None:
        """Ordering matters: 'Preferred Qualifications' is not a must-have.

        Treating it as one inflates every gap list with things the employer
        explicitly said were optional.
        """
        assert classify_heading("Preferred Qualifications") == SectionKind.NICE_TO_HAVE

    def test_boilerplate_kinds(self) -> None:
        assert classify_heading("BENEFITS") == SectionKind.BENEFITS
        assert classify_heading("Equal Opportunity") == SectionKind.LEGAL

    def test_unknown_heading_falls_back_to_other(self) -> None:
        assert classify_heading("Zorblatt") == SectionKind.OTHER

    def test_about_headings_win_over_overlapping_vocabulary(self) -> None:
        """ "ABOUT THE ROLE" is a company blurb, not the duties list.

        The RESPONSIBILITIES pattern legitimately matches "the role", so without
        an anchored ABOUT rule ahead of it every JD using this common heading had
        its overview indexed as responsibilities.
        """
        for heading in ("ABOUT THE ROLE", "About us", "About The Company"):
            assert classify_heading(heading) == SectionKind.ABOUT, heading

    def test_responsibilities_headings_still_classify(self) -> None:
        for heading in ("RESPONSIBILITIES", "What You'll Do", "Duties"):
            assert classify_heading(heading) == SectionKind.RESPONSIBILITIES, heading


class TestDetection:
    def test_sections_are_contiguous_and_cover_the_whole_document(self) -> None:
        """No character may be silently dropped by a heuristic that guessed wrong."""
        sections = detect_sections(RESUME)

        assert sections[0].char_start == 0
        assert sections[-1].char_end == len(RESUME)
        for earlier, later in pairwise(sections):
            assert earlier.char_end == later.char_start

    def test_offsets_slice_back_to_the_heading(self) -> None:
        """The offset contract, at section granularity."""
        for section in detect_sections(RESUME):
            if section.heading:
                assert (
                    RESUME[section.char_start : section.char_end]
                    .lstrip()
                    .startswith(section.heading)
                )

    def test_resume_sections_are_identified(self) -> None:
        kinds = {s.kind for s in detect_sections(RESUME)}

        assert SectionKind.EXPERIENCE in kinds
        assert SectionKind.EDUCATION in kinds
        assert SectionKind.SKILLS in kinds

    def test_preamble_before_the_first_heading_is_kept(self) -> None:
        """On a résumé that block is the candidate's name and contact details."""
        first = detect_sections(RESUME)[0]

        assert first.char_start == 0
        assert "ALEX MORAN" in RESUME[first.char_start : first.char_end]

    def test_benefits_and_legal_are_flagged_as_boilerplate(self) -> None:
        boilerplate = [s for s in detect_sections(JOB) if s.is_boilerplate]

        kinds = {s.kind for s in boilerplate}
        assert kinds == {SectionKind.BENEFITS, SectionKind.LEGAL}

    def test_requirements_are_not_boilerplate(self) -> None:
        requirements = [s for s in detect_sections(JOB) if s.kind == SectionKind.REQUIREMENTS]

        assert len(requirements) == 1
        assert not requirements[0].is_boilerplate
        assert "Kubernetes" in JOB[requirements[0].char_start : requirements[0].char_end]

    def test_a_document_with_no_headings_becomes_one_section(self) -> None:
        """Terse pasted JDs are common and must still be retrievable."""
        text = "We need someone who knows Python and can write tests."

        sections = detect_sections(text)

        assert len(sections) == 1
        assert sections[0].char_start == 0
        assert sections[0].char_end == len(text)

    def test_empty_document_yields_no_sections(self) -> None:
        assert detect_sections("") == []
        assert detect_sections("   \n\n  ") == []
