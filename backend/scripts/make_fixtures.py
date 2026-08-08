"""Generate the demo PDF corpus.

Run: `docker compose exec api python scripts/make_fixtures.py`

The generated PDFs are committed, so tests and the demo never depend on
regenerating them. This script is committed too, so a reviewer can see exactly
what is inside those binaries — including the injection payload, which is the
one file where "trust me, it's synthetic" would not be good enough.
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.colors import black, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture_content import (
    ADVERSARIAL_PAYLOAD,
    ADVERSARIAL_VISIBLE,
    JOB_HELIO,
    JOB_NORTHWIND,
    JOB_VERTEX,
    RESUME,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 56
LEADING = 13.5
BODY_SIZE = 10
MAX_CHARS_PER_LINE = 92


def write_pdf(path: Path, body: str, *, hidden: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setTitle(path.stem)

    y = PAGE_HEIGHT - MARGIN
    pdf.setFont("Helvetica", BODY_SIZE)
    pdf.setFillColor(black)

    for line in _wrapped_lines(body):
        if y < MARGIN:
            pdf.showPage()
            pdf.setFont("Helvetica", BODY_SIZE)
            pdf.setFillColor(black)
            y = PAGE_HEIGHT - MARGIN
        pdf.drawString(MARGIN, y, line)
        y -= LEADING

    if hidden:
        # White on a white page: invisible to a human reviewer, fully present in
        # the text layer, and therefore fully present in the model's context.
        # This is what apps/documents/parsers/pdf.py detects via per-character
        # non_stroking_color.
        pdf.setFillColor(white)
        pdf.setFont("Helvetica", BODY_SIZE)
        for line in _wrapped_lines(hidden):
            if y < MARGIN:
                pdf.showPage()
                pdf.setFillColor(white)
                pdf.setFont("Helvetica", BODY_SIZE)
                y = PAGE_HEIGHT - MARGIN
            pdf.drawString(MARGIN, y, line)
            y -= LEADING

    pdf.save()


def _wrapped_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        if len(raw) <= MAX_CHARS_PER_LINE:
            lines.append(raw)
            continue
        indent = " " * (len(raw) - len(raw.lstrip()))
        current = indent
        for word in raw.split():
            candidate = f"{current}{word} " if current.strip() else f"{indent}{word} "
            if len(candidate) > MAX_CHARS_PER_LINE:
                lines.append(current.rstrip())
                current = f"{indent}{word} "
            else:
                current = candidate
        if current.strip():
            lines.append(current.rstrip())
    return lines


def main() -> None:
    write_pdf(FIXTURES / "demo" / "resume.pdf", RESUME)
    write_pdf(FIXTURES / "demo" / "job_1_northwind.pdf", JOB_NORTHWIND)
    write_pdf(FIXTURES / "demo" / "job_2_vertex.pdf", JOB_VERTEX)
    write_pdf(FIXTURES / "demo" / "job_3_helio.pdf", JOB_HELIO)
    write_pdf(
        FIXTURES / "adversarial_job.pdf",
        ADVERSARIAL_VISIBLE,
        hidden=ADVERSARIAL_PAYLOAD,
    )

    for path in sorted(FIXTURES.rglob("*.pdf")):
        print(f"  {path.relative_to(FIXTURES)}  {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
