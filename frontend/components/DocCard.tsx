"use client";

/**
 * One document in the rail.
 *
 * Two things here are load-bearing rather than decorative.
 *
 * The **ordinal badge** shows "①" for Job #1 because "Job #2" is a phrase the
 * user is expected to type — the scope resolver reads it server-side and routes
 * retrieval on it. If the rail does not show the number, the feature is
 * undiscoverable.
 *
 * The **quarantine warning** is a product feature, not a log line. A silent
 * filter is a guardrail; an auditable one lets the user see that a posting
 * contained text aimed at an automated screen, which is worth knowing about the
 * employer.
 */

import { AlertTriangle, FileText, Trash2 } from "lucide-react";

import type { Document } from "@/lib/types";

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const ORDINALS = ["", "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"];

interface Props {
  document: Document;
  onRemove: (document: Document) => void;
  /** Roving tabindex: the rail is one Tab stop, arrows move within it. */
  tabIndex: number;
  onFocus: () => void;
}

export function DocCard({ document, onRemove, tabIndex, onFocus }: Props) {
  const isJob = document.kind === "job";
  const badge = isJob ? (ORDINALS[document.ordinal] ?? `#${document.ordinal}`) : null;

  return (
    <li
      className="group relative rounded-card border border-hairline bg-card p-3 shadow-raised transition-colors duration-(--duration-hover) focus-within:border-accent"
      tabIndex={tabIndex}
      onFocus={onFocus}
      aria-label={`${document.label}, ${document.chunk_count} indexed passages`}
    >
      <div className="flex items-start gap-2">
        <span aria-hidden className="mt-0.5 shrink-0 text-ink-2">
          {badge ? (
            <span className="text-meta tabular">{badge}</span>
          ) : (
            <FileText size={14} strokeWidth={2} />
          )}
        </span>

        <div className="min-w-0 flex-1">
          <p className="truncate text-body font-medium" title={document.label}>
            {document.label}
          </p>
          <p className="mt-0.5 truncate text-micro text-ink-2">
            {/* Rendered as text, never as HTML: the filename came from the
                user's disk and is escaped by React on the way in. */}
            {document.original_filename || "pasted text"}
            {" · "}
            <span data-numeric>{document.chunk_count}</span> passages
            {document.size_bytes > 0 ? ` · ${humanSize(document.size_bytes)}` : ""}
          </p>
        </div>

        <button
          type="button"
          onClick={() => onRemove(document)}
          // Visible on hover for the mouse, always present for the keyboard —
          // opacity-0 still receives focus, `hidden` would not.
          className="shrink-0 rounded-control p-1 text-ink-2 opacity-0 transition-opacity duration-(--duration-hover) hover:text-gap focus-visible:opacity-100 group-hover:opacity-100"
          aria-label={`Remove ${document.label}`}
        >
          <Trash2 size={14} strokeWidth={2} aria-hidden />
        </button>
      </div>

      {document.injection_flag ? (
        <p className="mt-2 flex items-start gap-1.5 rounded-control bg-muted p-2 text-micro text-ink">
          {/* Filled, not a hairline stroke: the icon is a graphical object under
              SC 1.4.11 and has to clear 3:1 in its own right. See adr/0011. */}
          <AlertTriangle
            size={13}
            className="mt-px shrink-0 text-weak"
            fill="currentColor"
            strokeWidth={0}
            aria-hidden
          />
          <span>
            Suspicious instructions detected. Those passages are excluded from
            retrieval.
          </span>
        </p>
      ) : null}
    </li>
  );
}
