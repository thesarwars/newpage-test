import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DocCard } from "@/components/DocCard";
import type { Document } from "@/lib/types";

const base: Document = {
  id: "doc-1",
  kind: "job",
  ordinal: 2,
  label: "Platform Engineer — Helios",
  company: "",
  original_filename: "job_2.pdf",
  page_count: 2,
  size_bytes: 41_000,
  chunk_count: 8,
  status: "ready",
  error_code: "",
  injection_flag: false,
  injection_reasons: [],
  sections: [],
  created_at: "2026-08-08T00:00:00+00:00",
};

function renderCard(document: Partial<Document> = {}) {
  const onRemove = vi.fn();
  render(
    <ul>
      <DocCard
        document={{ ...base, ...document }}
        onRemove={onRemove}
        tabIndex={0}
        onFocus={vi.fn()}
      />
    </ul>,
  );
  return { onRemove };
}

describe("document card", () => {
  it("shows the job number, because the user is expected to type it", () => {
    // "Job #2" is resolved server-side and routes retrieval. If the rail does
    // not show the number, the feature is undiscoverable.
    renderCard();

    expect(screen.getByText("②")).toBeInTheDocument();
  });

  it("shows no number for the résumé", () => {
    renderCard({ kind: "resume", ordinal: 0, label: "Résumé" });

    expect(screen.queryByText("①")).not.toBeInTheDocument();
  });

  it("reports how many passages were indexed", () => {
    // The cheapest available evidence that indexing actually happened, rather
    // than the server merely reporting that it did.
    renderCard();

    expect(screen.getByText("8")).toHaveAttribute("data-numeric");
    expect(screen.getByText(/passages/)).toBeInTheDocument();
  });

  it("surfaces a quarantined document rather than hiding it", () => {
    // A silent filter is a guardrail; an auditable one tells the user something
    // worth knowing about the employer.
    renderCard({ injection_flag: true, injection_reasons: ["imperative_override"] });

    expect(screen.getByText(/Suspicious instructions detected/)).toBeInTheDocument();
    expect(screen.getByText(/excluded from retrieval/)).toBeInTheDocument();
  });

  it("says nothing when the document is clean", () => {
    renderCard();

    expect(screen.queryByText(/Suspicious/)).not.toBeInTheDocument();
  });

  it("names the document in its remove button", async () => {
    // "Remove" alone is ambiguous in a list of ten; a screen-reader user needs
    // to know which one they are about to delete.
    const user = userEvent.setup();
    const { onRemove } = renderCard();

    await user.click(
      screen.getByRole("button", { name: "Remove Platform Engineer — Helios" }),
    );

    expect(onRemove).toHaveBeenCalledOnce();
  });

  it("renders a filename as text, never as markup", () => {
    renderCard({ original_filename: "<img src=x onerror=alert(1)>.pdf" });

    expect(screen.getByText(/<img src=x onerror=alert\(1\)>\.pdf/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});
