"""Chunking: the offset invariant, the encoder ceiling, and golden snapshots.

The first test in this file is the one the evidence-panel feature rests on. If
`normalized_text[c.char_start:c.char_end] != c.text`, every citation highlights
the wrong span — and nothing raises, logs, or looks wrong until a human reads a
quotation that does not match the sentence it points at.
"""

from __future__ import annotations

from itertools import pairwise

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apps.documents.chunking.breadcrumb import MAX_BREADCRUMB_TOKENS
from apps.documents.chunking.breadcrumb import apply as apply_breadcrumb
from apps.documents.chunking.breadcrumb import build as build_breadcrumb
from apps.documents.chunking.sections import detect_sections
from apps.documents.chunking.splitter import (
    MAX_CHUNK_TOKENS,
    TARGET_TOKENS,
    assert_fits_encoder,
    split_document,
)
from apps.documents.chunking.tokenizer import (
    MAX_SEQUENCE_TOKENS,
    count_tokens,
    truncate_to_tokens,
)
from apps.documents.normalize import normalize

RESUME = normalize("""\
ALEX MORAN
Backend Engineer

EXPERIENCE

Senior Backend Engineer, Meridian Logistics — 2022 to Present
- Rebuilt the shipment tracking service in Go, cutting p99 latency from 1.4s to 380ms.
- Designed the PostgreSQL schema for a multi-tenant carrier integration.
- Introduced contract tests between the tracking service and three consumers.

Backend Engineer, Halloway Payments — 2020 to 2022
- Built the reconciliation pipeline matching card settlements against ledger entries.
- Migrated the ledger from MySQL to PostgreSQL with no downtime.

SKILLS
Python, Go, SQL, PostgreSQL, Redis, Kafka, Docker, AWS
""").text

JOB = normalize("""\
Staff Backend Engineer

REQUIREMENTS
- 6+ years in backend or platform engineering.
- Production Kubernetes experience, including cluster upgrades.
- Terraform in production, managing real infrastructure state.
- Strong Go.

BENEFITS
- Fully remote within the UK.
- Learning budget of GBP 2,000 a year.
""").text


def _chunk(text: str, kind: str = "job") -> list:
    return split_document(text, detect_sections(text), kind=kind)


class TestOffsetInvariant:
    """The contract every citation depends on."""

    def test_resume_chunks_slice_back_exactly(self) -> None:
        for chunk in _chunk(RESUME, kind="resume"):
            assert RESUME[chunk.char_start : chunk.char_end] == chunk.text

    def test_job_chunks_slice_back_exactly(self) -> None:
        for chunk in _chunk(JOB, kind="job"):
            assert JOB[chunk.char_start : chunk.char_end] == chunk.text

    @settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow], deadline=None)
    @given(
        st.text(
            alphabet=st.sampled_from(list("abc XYZ\n-.,•—0123456789")),
            min_size=0,
            max_size=600,
        )
    )
    def test_invariant_holds_for_arbitrary_documents(self, raw: str) -> None:
        """Property form: no input shape may break the mapping.

        Handwritten examples cannot find the cases that matter here — a document
        that is all bullets, one with no blank lines, one whose section boundary
        lands mid-bullet.
        """
        text = normalize(raw).text

        for chunk in split_document(text, detect_sections(text), kind="job"):
            assert text[chunk.char_start : chunk.char_end] == chunk.text

    @settings(max_examples=40, suppress_health_check=[HealthCheck.too_slow], deadline=None)
    @given(
        st.text(
            alphabet=st.sampled_from(list("abc XYZ\n-.,")),
            min_size=0,
            max_size=400,
        )
    )
    def test_chunks_never_overlap_and_advance(self, raw: str) -> None:
        """Overlapping chunks would cite the same characters twice.

        Two citations pointing at one span render as two separate pieces of
        evidence in the panel, which is a lie about how much support an answer
        has.
        """
        text = normalize(raw).text
        chunks = split_document(text, detect_sections(text), kind="job")

        for earlier, later in pairwise(chunks):
            assert earlier.char_end <= later.char_start, "chunks overlap"

    def test_ordinals_are_dense_and_ordered(self) -> None:
        chunks = _chunk(RESUME, kind="resume")

        assert [c.ordinal for c in chunks] == list(range(len(chunks)))


class TestEncoderCeiling:
    """Silent truncation is the failure mode with no symptom."""

    def test_every_embed_text_fits_the_encoder(self) -> None:
        for text, kind in ((RESUME, "resume"), (JOB, "job")):
            for chunk in _chunk(text, kind=kind):
                embed_text = apply_breadcrumb(
                    build_breadcrumb(
                        document_label="Job #2",
                        section_heading="Requirements",
                        leading_line=chunk.leading_line,
                    ),
                    chunk.text,
                )
                assert count_tokens(embed_text) <= MAX_SEQUENCE_TOKENS

    def test_assert_fits_encoder_raises_rather_than_warns(self) -> None:
        """A chunk over the limit must fail loudly.

        bge-small drops the tail past 512 tokens without error, so the tail
        becomes invisible to the index while retrieval quietly degrades.
        """
        oversized = "kubernetes terraform postgresql " * 200

        try:
            assert_fits_encoder(oversized)
        except ValueError as exc:
            assert "silently dropped" in str(exc)
        else:
            raise AssertionError("oversized embed_text was accepted")

    def test_chunks_respect_the_hard_ceiling(self) -> None:
        for chunk in _chunk(RESUME, kind="resume"):
            assert chunk.token_count <= MAX_CHUNK_TOKENS

    def test_a_single_oversized_unit_is_split_rather_than_truncated(self) -> None:
        """One 2000-token 'bullet' must become several chunks, losing nothing."""
        monster = "REQUIREMENTS\n- " + ("kubernetes terraform postgresql go " * 300)
        text = normalize(monster).text

        chunks = split_document(text, detect_sections(text), kind="job")

        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.token_count <= MAX_CHUNK_TOKENS
            assert text[chunk.char_start : chunk.char_end] == chunk.text


class TestAtomicUnits:
    def test_requirement_bullets_are_never_cut_in_half(self) -> None:
        """Half a requirement retrieves as noise.

        "5+ years of production Kubernetes" is one claim; splitting it yields
        two fragments that each match weakly and neither of which is true.
        """
        for chunk in _chunk(JOB, kind="job"):
            for line in chunk.text.strip().splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("- "):
                    continue
                assert stripped.endswith((".", ":", "!")) or len(stripped) < 8, (
                    f"bullet looks truncated: {stripped!r}"
                )

    def test_chunks_never_straddle_a_section(self) -> None:
        """A chunk that is half requirements and half EEO text cannot be
        excluded as boilerplate, and gets one wrong breadcrumb."""
        sections = detect_sections(JOB)
        chunks = split_document(JOB, sections, kind="job")

        for chunk in chunks:
            section = sections[chunk.section_index]
            assert section.char_start <= chunk.char_start
            assert chunk.char_end <= section.char_end


class TestGoldenSnapshots:
    """Pinned shape, so a chunker change is a visible diff rather than a surprise."""

    def test_job_requirements_chunk_keeps_its_bullets_together(self) -> None:
        chunks = _chunk(JOB, kind="job")

        requirements = [c for c in chunks if "6+ years" in c.text]
        assert len(requirements) == 1
        assert "Terraform in production" in requirements[0].text

    def test_boilerplate_lands_in_its_own_chunks(self) -> None:
        sections = detect_sections(JOB)
        chunks = split_document(JOB, sections, kind="job")

        benefits = [c for c in chunks if sections[c.section_index].is_boilerplate]
        assert benefits
        assert all("Kubernetes" not in c.text for c in benefits)

    def test_resume_roles_do_not_merge_into_one_chunk(self) -> None:
        """Averaging two employers into one vector is the failure this avoids."""
        chunks = _chunk(RESUME, kind="resume")

        merged = [c for c in chunks if "Meridian" in c.text and "Halloway" in c.text]
        assert not merged, "two employers ended up in a single chunk"

    def test_per_kind_targets_are_distinct(self) -> None:
        assert TARGET_TOKENS["resume"] > TARGET_TOKENS["job"]


class TestBreadcrumbs:
    def test_breadcrumb_carries_document_and_section(self) -> None:
        crumb = build_breadcrumb(document_label="Job #2", section_heading="REQUIREMENTS")

        assert "Job #2" in crumb
        assert "REQUIREMENTS" in crumb
        assert crumb.startswith("[") and crumb.endswith("]")

    def test_breadcrumb_recovers_the_employer_inside_experience(self) -> None:
        """ "Reduced p99 latency 40%" is unattributable without this."""
        crumb = build_breadcrumb(
            document_label="Résumé",
            section_heading="Experience",
            leading_line="Senior Backend Engineer, Meridian Logistics — 2022 to Present",
        )

        assert "Meridian Logistics" in crumb

    def test_a_bullet_is_not_mistaken_for_a_role_header(self) -> None:
        crumb = build_breadcrumb(
            document_label="Résumé",
            section_heading="Experience",
            leading_line="- Rebuilt the shipment tracking service in Go.",
        )

        assert "Rebuilt" not in crumb

    def test_breadcrumb_stays_within_its_token_budget(self) -> None:
        """Budgeted inside the 512 limit, not on top of it."""
        crumb = build_breadcrumb(
            document_label="A very long document label " * 10,
            section_heading="An equally long section heading " * 10,
            leading_line="Staff Engineer, Some Company With A Long Name — 2020 to 2024",
        )

        assert count_tokens(crumb) <= MAX_BREADCRUMB_TOKENS

    def test_apply_leaves_raw_text_untouched(self) -> None:
        """Chunk.text must stay byte-identical — offsets index into it."""
        assert apply_breadcrumb("[Résumé — Skills]", "Python, Go").endswith("Python, Go")
        assert apply_breadcrumb("", "Python, Go") == "Python, Go"


class TestTokenizer:
    def test_counts_are_real_not_a_character_proxy(self) -> None:
        """The proxy under-counts dense technical text, which silently truncates.

        A 4-chars-per-token estimate reads this sentence as ~14 tokens; the
        encoder sees 17. Scaled to a 512-token budget that is ~100 tokens of a
        chunk's tail dropped with no error.
        """
        text = "Production Kubernetes experience, not just running kubectl."

        assert count_tokens(text) > len(text) // 4

    def test_empty_text_is_zero_tokens(self) -> None:
        assert count_tokens("") == 0

    def test_truncate_lands_on_a_token_boundary(self) -> None:
        text = "kubernetes terraform postgresql golang " * 50

        truncated = truncate_to_tokens(text, 64)

        assert count_tokens(truncated) <= 64
        assert text.startswith(truncated)

    def test_truncate_is_a_noop_when_already_short(self) -> None:
        assert truncate_to_tokens("short text", 512) == "short text"
