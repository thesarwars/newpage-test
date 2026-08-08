"use client";

/**
 * The evidence panel: the document, with the cited span highlighted.
 *
 * This is where the whole citation chain becomes visible, and where it would be
 * visibly wrong if any link in it were broken. The offsets come from Anthropic's
 * own `char_location`, rebased onto `Document.normalized_text` by
 * `apps/chat/citations.py`, and verified server-side against the stored text
 * before they were ever persisted — a citation whose slice does not equal its
 * `cited_text` is dropped rather than shown. So by the time one reaches here,
 * `normalized_text.slice(char_start, char_end)` is the cited text, and this
 * component's only job is not to lose that.
 *
 * Which is why the text is sliced by index rather than searched for. A search
 * would find the first occurrence of "Kubernetes" in a job description that
 * mentions it four times, and would look right in a screenshot.
 *
 * Not a dialog: it is a persistent side region, so it does not trap focus, does
 * not block the page, and does not steal focus when it opens. Escape closes it
 * and returns focus to the citation that opened it, which is the pattern for a
 * non-modal panel.
 */

import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { toUtf16 } from "@/lib/offsets";
import { useUi } from "@/lib/store";
import type { Document } from "@/lib/types";

export function EvidencePanel() {
  const selected = useUi((s) => s.selected);
  const open = useUi((s) => s.panelOpen);
  const close = useUi((s) => s.closePanel);

  const [document, setDocument] = useState<Document | null>(null);
  const [error, setError] = useState<string | null>(null);
  const markRef = useRef<HTMLElement>(null);

  const docId = selected?.docId ?? null;

  useEffect(() => {
    if (!docId) return;
    let cancelled = false;

    void (async () => {
      try {
        const fetched = await api.document(docId);
        if (!cancelled) {
          setDocument(fetched);
          setError(null);
        }
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof ApiError ? cause.message : "Could not load the document.");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [docId]);

  // Scroll the highlight into view once the text is on screen. `block: center`
  // rather than the default `start`, because a span pinned to the top edge of a
  // scroll container reads as "the document begins here".
  useEffect(() => {
    if (!open || !markRef.current) return;
    markRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [open, selected, document]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      close();
      // Focus goes back to the citation that opened the panel. Without this the
      // keyboard user is returned to the top of the document.
      const source = window.document.querySelector<HTMLElement>(
        `[data-citation="${selected?.index}"]`,
      );
      source?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close, selected]);

  if (!open || !selected) {
    return (
      <div className="grid h-full place-items-center p-6 text-center">
        <p className="max-w-[18rem] text-meta text-ink-2">
          Click a citation in an answer to see the exact passage it came from.
        </p>
      </div>
    );
  }

  const text = document?.normalized_text ?? "";
  // The server's offsets are Python code-point indices into this exact string;
  // `slice` works in UTF-16 units. They coincide for BMP text and diverge by one
  // per astral character — an emoji in a pasted job description is enough.
  const start = toUtf16(text, selected.charStart);
  const end = toUtf16(text, selected.charEnd);
  const before = text.slice(0, start);
  const cited = text.slice(start, end);
  const after = text.slice(end);
  // The server already guarantees this. Checking anyway costs one comparison and
  // turns a silent mis-highlight into a visible warning — a wrong highlight is
  // worse than a missing one, because it looks like evidence.
  const trustworthy = !document || cited === selected.citedText;

  return (
    <section aria-label="Evidence" className="flex h-full flex-col">
      <header className="flex items-start justify-between gap-2 border-b border-hairline p-3">
        <div className="min-w-0">
          <p className="truncate text-meta font-medium">
            {document?.label ?? "Loading…"}
          </p>
          <p className="mt-0.5 font-mono text-micro text-ink-2" data-numeric>
            characters {selected.charStart}–{selected.charEnd}
          </p>
        </div>
        <button
          type="button"
          onClick={close}
          aria-label="Close evidence panel"
          className="shrink-0 rounded-control p-1 text-ink-2 hover:bg-muted hover:text-ink"
        >
          <X size={14} aria-hidden />
        </button>
      </header>

      {error ? (
        <p className="p-3 text-meta text-ink-2">{error}</p>
      ) : !trustworthy ? (
        <p className="m-3 rounded-control border border-hairline bg-muted p-2 text-micro">
          The stored text no longer matches this citation, so the highlight is
          not shown. The document may have been re-uploaded since this answer.
        </p>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto p-3">
          {/* `pre-wrap` rather than `pre`: normalized_text has real line
              structure worth preserving, but a job description contains lines
              far wider than a 380px panel, and `pre` would scroll the whole
              layout sideways. */}
          <pre className="whitespace-pre-wrap break-words font-mono text-micro leading-[1.7] text-ink-2">
            {before}
            <mark
              ref={markRef}
              className="rounded-[3px] bg-mark px-0.5 text-ink"
              // Announced as a region so a screen-reader user can find the
              // highlight rather than reading the document to locate it.
              aria-label={`Cited passage: ${cited}`}
            >
              {cited}
            </mark>
            {after}
          </pre>
        </div>
      )}
    </section>
  );
}
