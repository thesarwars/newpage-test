"use client";

/**
 * The paste fallback.
 *
 * Every parse failure the server can raise ends its hint with "or paste the
 * text instead", and a 422 that names a fallback with nowhere to perform it is
 * a dead end on the first scanned PDF. This is that somewhere.
 *
 * It is also the only path that works for a posting that lives on a web page
 * rather than in a file, which is most of them.
 */

import { useEffect, useRef, useState } from "react";

import { MAX_PASTED_CHARS, MIN_PASTED_CHARS } from "@/lib/constants";

interface Props {
  open: boolean;
  busy: boolean;
  defaultKind: "resume" | "job";
  canAddResume: boolean;
  onSubmit: (text: string, kind: "resume" | "job", label: string) => void;
  onCancel: () => void;
}

export function PasteSheet({
  open,
  busy,
  defaultKind,
  canAddResume,
  onSubmit,
  onCancel,
}: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [text, setText] = useState("");
  const [kind, setKind] = useState<"resume" | "job">(defaultKind);
  const [label, setLabel] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      setKind(defaultKind);
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open, defaultKind]);

  const trimmed = text.trim().length;
  // Both bounds are the server's, mirrored so the button explains itself before
  // the round trip rather than after it. The server still enforces them.
  const tooShort = trimmed > 0 && trimmed < MIN_PASTED_CHARS;
  const tooLong = text.length > MAX_PASTED_CHARS;
  const submittable = trimmed >= MIN_PASTED_CHARS && !tooLong && !busy;

  return (
    <dialog
      ref={ref}
      aria-labelledby="paste-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
      className="m-auto w-[min(44rem,calc(100vw-2rem))] rounded-card border border-hairline bg-card p-0 text-ink shadow-raised backdrop:bg-black/40"
    >
      <form
        method="dialog"
        onSubmit={(event) => {
          event.preventDefault();
          if (submittable) onSubmit(text, kind, label.trim());
        }}
        className="p-5"
      >
        <h2 id="paste-title" className="text-title font-semibold">
          Paste text
        </h2>
        <p className="mt-1 text-meta text-ink-2">
          For a scanned PDF, or a posting that only exists on a web page.
        </p>

        <fieldset className="mt-4">
          <legend className="sr-only">Document type</legend>
          <div className="flex gap-2">
            {(["job", "resume"] as const).map((option) => (
              <label
                key={option}
                className={`cursor-pointer rounded-pill border px-3 py-1 text-meta transition-colors duration-(--duration-hover) ${
                  kind === option
                    ? "border-accent bg-muted"
                    : "border-hairline hover:bg-muted"
                } ${option === "resume" && !canAddResume ? "cursor-not-allowed opacity-40" : ""}`}
              >
                <input
                  type="radio"
                  name="kind"
                  className="sr-only"
                  checked={kind === option}
                  disabled={option === "resume" && !canAddResume}
                  onChange={() => setKind(option)}
                />
                {option === "resume" ? "Résumé" : "Job description"}
              </label>
            ))}
          </div>
        </fieldset>

        <label className="mt-4 block">
          <span className="text-meta text-ink-2">Label (optional)</span>
          <input
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder={kind === "job" ? "Senior Platform Engineer — Helios" : "Résumé"}
            className="mt-1 w-full rounded-control border border-hairline-strong bg-surface px-2 py-1.5 text-body"
          />
        </label>

        <label className="mt-4 block">
          <span className="text-meta text-ink-2">Text</span>
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            rows={12}
            // Monospace: this is document text, and the app renders document
            // text in mono everywhere so that what you paste looks like what
            // the evidence panel will later highlight.
            className="mt-1 w-full rounded-control border border-hairline-strong bg-surface px-2 py-1.5 font-mono text-micro"
            placeholder="Paste the whole posting, including the requirements section."
            // Cmd/Ctrl+Enter submits, matching the send binding the design
            // system reserves for the composer.
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && submittable) {
                event.preventDefault();
                onSubmit(text, kind, label.trim());
              }
            }}
          />
        </label>

        <p aria-live="polite" className="mt-1 min-h-4 text-micro text-ink-2">
          {tooShort
            ? `${MIN_PASTED_CHARS - trimmed} more characters needed.`
            : tooLong
              ? `That's ${(text.length - MAX_PASTED_CHARS).toLocaleString()} characters over the limit.`
              : trimmed > 0
                ? `${trimmed.toLocaleString()} characters`
                : ""}
        </p>

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-control border border-hairline-strong px-3 py-1.5 text-body hover:bg-muted disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!submittable}
            className="rounded-control bg-accent-fill px-3 py-1.5 text-body text-white disabled:opacity-50"
          >
            {busy ? "Reading…" : "Add document"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
