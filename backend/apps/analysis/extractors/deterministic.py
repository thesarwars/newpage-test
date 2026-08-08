"""Keyless requirement extraction.

Regex over the bullets of REQUIREMENTS / QUALIFICATIONS / NICE-TO-HAVE sections,
with the skill resolved against the alias map. Blunter than an LLM and available
to everyone — which is the point: this is what makes the Fit Board and Gap Matrix
work on a reviewer's own uploads with no API key.

It is measured rather than assumed. The eval reports Gap-F1 three ways — naive
top-k retrieval, this extractor, and the LLM extractor — so the gap between them
is a published number rather than a hand-wave.

Known limits, stated rather than hidden: a posting that writes its requirements
as prose instead of bullets yields little, and `must_have` is inferred from
wording rather than understood. Both show up in the eval numbers.
"""

from __future__ import annotations

import re

from apps.analysis.extractors.base import ExtractedRequirement
from apps.analysis.models import ExtractorSource, RequirementCategory
from apps.documents.chunking.sections import Section, SectionKind
from apps.rag.aliases import ALIASES, normalize_skill

# Sections worth reading. RESPONSIBILITIES describes the job rather than the
# candidate, so it is excluded: "own the deployment pipeline" is a duty, and
# listing it as a missing skill would be wrong.
_REQUIREMENT_SECTIONS = frozenset({SectionKind.REQUIREMENTS, SectionKind.NICE_TO_HAVE})

# Wording that marks a requirement as optional. Checked before the must-have
# patterns, because "Kubernetes is required for the nice-to-have team" is not a
# sentence anyone writes, while "preferred" inside a REQUIREMENTS section is
# common.
_OPTIONAL = re.compile(
    r"\b(nice[\s-]?to[\s-]?have|preferred|preferably|bonus|a plus|desirable|ideally|would be great)\b",
    re.I,
)
_REQUIRED = re.compile(r"\b(required|must|essential|minimum|at least|\d+\+?\s*years?)\b", re.I)

_YEARS = re.compile(r"\b(\d+)\+?\s*years?\b", re.I)
_SENIORITY = re.compile(r"\b(senior|staff|principal|lead|junior|mid[\s-]?level)\b", re.I)
_CREDENTIAL = re.compile(r"\b(degree|bsc|msc|phd|bachelor|master|certifi|licen[cs]e)\b", re.I)
_SOFT = re.compile(
    r"\b(communicat|collaborat|mentor|leadership|stakeholder|team player|ownership|autonom)\b",
    re.I,
)
_DOMAIN = re.compile(
    r"\b(fintech|healthcare|logistics|e-?commerce|regulated|compliance|payments?|supply chain|ml|research)\b",
    re.I,
)

# Known multi-word skills, longest first so "google cloud" wins over "cloud".
_KNOWN_SKILLS: tuple[str, ...] = tuple(
    sorted(
        {
            *ALIASES.keys(),
            *ALIASES.values(),
            "python",
            "go",
            "java",
            "ruby",
            "rust",
            "scala",
            "kotlin",
            "sql",
            "kubernetes",
            "docker",
            "terraform",
            "ansible",
            "postgresql",
            "mysql",
            "redis",
            "kafka",
            "rabbitmq",
            "elasticsearch",
            "mongodb",
            "spark",
            "airflow",
            "dbt",
            "snowflake",
            "grpc",
            "rest",
            "graphql",
            "microservices",
            "distributed systems",
            "event driven",
            "observability",
            "ci/cd",
            "aws",
            "google cloud",
            "azure",
            "linux",
            "networking",
            "security",
            "pytorch",
            "tensorflow",
            "cuda",
            "kubeflow",
            "ray",
            "mlops",
            "vector databases",
            "feature store",
            "model serving",
            "on-call",
            "schema design",
            "query tuning",
            "database migrations",
            "unit testing",
        },
        key=len,
        reverse=True,
    )
)
_KNOWN_SKILL_SET = frozenset(_KNOWN_SKILLS)


def is_curated(skill: str) -> bool:
    """Whether this skill name came from the vocabulary rather than a guess.

    The distinction matters wherever a skill is shown to a user. A curated hit
    ("kubernetes") is a named technology; a fallback hit ("backend services") is
    a noun phrase lifted out of prose, which is good enough to index on and not
    good enough to build a sentence around.
    """
    from apps.rag.aliases import normalize_skill

    return skill in _KNOWN_SKILL_SET or any(
        normalize_skill(known) == skill for known in _KNOWN_SKILLS
    )


# A bullet shorter than this is a fragment — only applied when no known skill
# was recognised in it.
_MIN_BULLET_CHARS = 12


class DeterministicExtractor:
    """Regex-based extraction. No network, no key, no model."""

    @property
    def source(self) -> str:
        return ExtractorSource.DETERMINISTIC

    def extract(
        self, *, normalized_text: str, sections: list[Section]
    ) -> list[ExtractedRequirement]:
        found: list[ExtractedRequirement] = []
        seen_skills: set[str] = set()

        for section in sections:
            if section.kind not in _REQUIREMENT_SECTIONS:
                continue

            section_is_optional = section.kind == SectionKind.NICE_TO_HAVE

            for start, end in _bullet_spans(normalized_text, section):
                text = normalized_text[start:end].strip().lstrip("- ").strip()
                skill = _primary_skill(text)

                # Length is only a guard against fragments, so it is checked
                # *after* skill resolution and only when nothing recognisable was
                # found. "Strong Go." is ten characters and a real must-have;
                # dropping it on length alone lost a requirement silently.
                if not skill:
                    continue
                if skill not in _KNOWN_SKILL_SET and len(text) < _MIN_BULLET_CHARS:
                    continue
                if skill in seen_skills:
                    continue
                seen_skills.add(skill)

                found.append(
                    ExtractedRequirement(
                        text=text,
                        skill=skill,
                        category=_categorize(text),
                        must_have=_is_must_have(text, section_is_optional=section_is_optional),
                        char_start=start,
                        char_end=end,
                    )
                )

        return found


def _bullet_spans(text: str, section: Section) -> list[tuple[int, int]]:
    """Bullet lines within a section, as offsets into `normalized_text`.

    Offsets are tracked by construction rather than recovered with `.find()` —
    the same bullet text can legitimately appear in two postings.
    """
    spans: list[tuple[int, int]] = []
    cursor = section.char_start
    body = text[section.char_start : section.char_end]

    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("- "):
            spans.append((cursor, cursor + len(line.rstrip("\n"))))
        cursor += len(line)

    return spans


def _primary_skill(text: str) -> str:
    """The skill a requirement is about.

    Longest known skill wins, so "google cloud platform" beats "cloud" and
    "distributed systems" beats neither word alone. Falls back to a leading noun
    phrase so a requirement about something not in the vocabulary still produces
    a row — a missing requirement is worse than an imprecisely-named one.

    **Known limitation, measured rather than assumed.** Longest-match names
    "Solid PostgreSQL, including schema design and query tuning" as
    `schema design`, because that phrase is three characters longer than
    `postgresql`. The row is then judged against the résumé under the wrong
    name, which can invent a gap the candidate does not have.

    Ranking by earliest position instead fixes that case and breaks two worse
    ones: "Strong Python, with production PyTorch experience" resolves to
    `python` (which the résumé has, so a real PyTorch gap disappears), and
    "an ML orchestration framework such as Kubeflow" resolves to
    `machine learning`. Measured on the eval, position-first moved gap recall
    from 1.000 to 0.867 — it loses more than it wins.

    Neither ordering is right, because the distinction is specificity rather
    than position or length: `postgresql` is a named technology and
    `schema design` is a generic activity, and nothing in a flat vocabulary
    encodes that. Fixing it properly means weighting the vocabulary, which is
    the LLM extractor's job in M8 — this is the deterministic floor, and its
    floor is honest about where it sits.
    """
    lowered = text.lower()
    for candidate in _KNOWN_SKILLS:
        if re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", lowered):
            return normalize_skill(candidate)

    return _fallback_skill(lowered)


# Filler that precedes the actual noun in requirement prose: "Experience with
# service mesh technologies" is about service mesh, not about "with".
_LEAD_FILLER = re.compile(
    r"^\W*(?:\d+\+?\s*years?\s*)?(?:of|in|with|and|or|to|a|an|the|experience|exposure|"
    r"familiarity|strong|solid|proven|deep|demonstrable|hands[\s-]?on|understanding|"
    r"knowledge|background|working|production|expertise|comfortable|ability|"
    r"building|owning|managing)\b\W*",
    re.I,
)
_TRAILING_CONJUNCTION = re.compile(r"\b(?:or|and|with|for|to|in|of)$", re.I)
# An *internal* conjunction ends the skill name rather than joining it. "6+ years
# in backend or platform engineering" is a requirement about backend; taking the
# first three words produced "backend or platform", which is not a skill, does
# not match anything in a résumé, and is rendered verbatim in the Gap Matrix and
# in the suggestion chips.
_INTERNAL_CONJUNCTION = frozenset({"or", "and", "&", "/"})
_STOPWORD_ONLY = re.compile(r"^(?:\w{1,2}|and|or|the|with|for|from)$", re.I)


def _fallback_skill(lowered: str) -> str:
    """Best-effort skill name when nothing in the vocabulary matched.

    A requirement about something outside the alias map still deserves a row —
    a missing requirement is worse than an imprecisely-named one — but the name
    has to be usable, because it is rendered in the Gap Matrix and matched
    against the résumé. Strips leading filler repeatedly (some bullets stack it:
    "Deep understanding of…") and refuses to end on a dangling conjunction.
    """
    trimmed = _YEARS.sub(" ", lowered).strip()
    for _ in range(4):
        stripped = _LEAD_FILLER.sub("", trimmed, count=1).strip()
        if stripped == trimmed:
            break
        trimmed = stripped

    # Cut at the first clause boundary before taking words. "Experience with
    # distributed training: data and model parallelism" is about distributed
    # training; spanning the colon produced "distributed training data".
    trimmed = re.split(r"[:;.(]", trimmed, maxsplit=1)[0]

    words: list[str] = []
    for word in re.split(r"[\s,]+", trimmed):
        if not word:
            continue
        if word in _INTERNAL_CONJUNCTION:
            break
        words.append(word)
        if len(words) == 3:
            break

    while words and _TRAILING_CONJUNCTION.fullmatch(words[-1]):
        words.pop()

    candidate = " ".join(words).strip()
    if not candidate or _STOPWORD_ONLY.fullmatch(candidate):
        return ""
    return normalize_skill(candidate)


def _categorize(text: str) -> RequirementCategory:
    if _CREDENTIAL.search(text):
        return RequirementCategory.CREDENTIAL
    if _SENIORITY.search(text) or _YEARS.search(text):
        return RequirementCategory.SENIORITY
    if _SOFT.search(text):
        return RequirementCategory.SOFT_SKILL
    if _DOMAIN.search(text):
        return RequirementCategory.DOMAIN
    return RequirementCategory.HARD_SKILL


def _is_must_have(text: str, *, section_is_optional: bool) -> bool:
    """Optional unless the section or the wording says otherwise.

    A bullet inside REQUIREMENTS is a must-have by default — most postings do not
    write "required" on every line, and demanding that word would mark almost
    everything optional and empty the gap list. Explicit hedging ("preferred",
    "a plus") and the NICE-TO-HAVE section both override it.
    """
    if _OPTIONAL.search(text):
        return False
    return not section_is_optional
