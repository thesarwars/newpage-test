"use client";

/**
 * The composer: one text input, a scope pill and a mode toggle.
 *
 * The scope pill is a retrieval metadata filter exposed as a product control.
 * Selecting jobs sets `scope.job_ids`, which becomes `document_id IN (…)` plus a
 * per-job quota server-side — and prose works too, because "for Job #2" is
 * resolved by the same server-side resolver and comes back on the `scope` event.
 * The pill snapping mid-stream is how the user sees that the system understood
 * them.
 *
 * The single-key shortcuts docs/PLAN.md §7 reserves (digits for scope, brackets
 * for citations) are deliberately **not** bound while focus is in this textarea.
 * WCAG 2.2 SC 2.1.4 exists for exactly this: a character-key shortcut that fires
 * while someone is typing is a trap, and here it would silently change which
 * documents their question is about.
 */

import { ArrowUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useUi } from "@/lib/store";
import type { Document, Suggestion } from "@/lib/types";

interface Props {
  jobs: Document[];
  suggestions: Suggestion[];
  disabled: boolean;
  onSend: (message: string) => void;
}

export function Composer({ jobs, suggestions, disabled, onSend }: Props) {
  const [value, setValue] = useState("");
  const scope = useUi((s) => s.scopeJobIds);
  const toggleScope = useUi((s) => s.toggleScope);
  const mode = useUi((s) => s.mode);
  const setMode = useUi((s) => s.setMode);
  const box = useRef<HTMLTextAreaElement>(null);

  // Grow with the content rather than scrolling a three-line box. Reset first,
  // or the height ratchets upward and never comes back down.
  useEffect(() => {
    const element = box.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
  }, [value]);

  const send = () => {
    const message = value.trim();
    if (!message || disabled) return;
    onSend(message);
    setValue("");
  };

  return (
    <div className="border-t border-hairline bg-surface p-3">
      <div className="mx-auto max-w-[46rem] space-y-2">
        {suggestions.length > 0 && !value ? (
          <ul className="flex flex-wrap gap-1.5">
            {suggestions.map((chip) => (
              <li key={chip.label}>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onSend(chip.message)}
                  // The chip's label is short; the question it sends is the real
                  // one. Naming both means a screen-reader user is not asked to
                  // guess what "What am I missing?" will actually ask.
                  aria-label={chip.message}
                  className="rounded-pill border border-hairline bg-card px-2.5 py-1 text-micro transition-colors duration-(--duration-hover) hover:border-accent disabled:opacity-50"
                >
                  {chip.label}
                </button>
              </li>
            ))}
          </ul>
        ) : null}

        <div className="rounded-card border border-hairline-strong bg-card focus-within:border-accent">
          <div className="flex flex-wrap items-center gap-1.5 border-b border-hairline px-2 py-1.5">
            <span className="text-micro text-ink-2">Ask about</span>
            <button
              type="button"
              onClick={() => useUi.getState().setScope([])}
              aria-pressed={scope.length === 0}
              className={`rounded-pill px-2 py-0.5 text-micro transition-colors duration-(--duration-hover) ${
                scope.length === 0 ? "bg-accent-fill text-white" : "bg-muted text-ink-2"
              }`}
            >
              All jobs
            </button>
            {jobs.map((job) => (
              <button
                key={job.id}
                type="button"
                onClick={() => toggleScope(job.id)}
                aria-pressed={scope.includes(job.id)}
                className={`max-w-[12rem] truncate rounded-pill px-2 py-0.5 text-micro transition-colors duration-(--duration-hover) ${
                  scope.includes(job.id) ? "bg-accent-fill text-white" : "bg-muted text-ink-2"
                }`}
              >
                #{job.ordinal} {job.label}
              </button>
            ))}

            <div className="ml-auto flex gap-0.5 rounded-pill bg-muted p-0.5">
              {(["analysis", "interview"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setMode(option)}
                  aria-pressed={mode === option}
                  className={`rounded-pill px-2 py-0.5 text-micro capitalize transition-colors duration-(--duration-hover) ${
                    mode === option ? "bg-card text-ink shadow-raised" : "text-ink-2"
                  }`}
                >
                  {option}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-end gap-2 p-2">
            <textarea
              ref={box}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={(event) => {
                // Enter sends; Shift+Enter and Cmd/Ctrl+Enter both insert or
                // send respectively. Cmd+Enter is the binding the design system
                // reserves, and Enter-to-send is what people expect from a
                // single-line-looking box.
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
              rows={1}
              disabled={disabled}
              placeholder={
                mode === "interview"
                  ? "Ask what they'll probe on…"
                  : "Ask about your fit, gaps, or which projects are relevant…"
              }
              aria-label="Ask a question about your documents"
              className="max-h-[200px] min-h-[24px] flex-1 resize-none bg-transparent text-body outline-none placeholder:text-ink-2"
            />
            <button
              type="button"
              onClick={send}
              disabled={disabled || !value.trim()}
              aria-label="Send"
              className="shrink-0 rounded-control bg-accent-fill p-1.5 text-white transition-opacity duration-(--duration-hover) disabled:opacity-40"
            >
              <ArrowUp size={14} aria-hidden />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
