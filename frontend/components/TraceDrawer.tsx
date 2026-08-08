"use client";

/**
 * Why those passages, and what the answer cost.
 *
 * Collapsed to one line by default. Expanded, it is the thing that turns "the
 * answer was bad" into "the relevant chunk ranked 19th in dense and lexical
 * never fired" — a statement you can act on. It is fetched lazily, because most
 * answers are never questioned.
 */

import { ChevronRight } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";

interface Trace {
  query: string;
  expanded_query: string;
  resolved_from: string;
  dense_hits: { chunk_id: string; rank: number; score: number }[];
  lexical_hits: { chunk_id: string; rank: number; score: number }[];
  selected_chunk_ids: string[];
  anchors_applied: string[];
  quota_applied: boolean;
  max_score: number;
  context_chars: number;
  timings_ms: Record<string, number>;
}

interface Call {
  model: string;
  backend: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string;
  latency_ms: number;
  ttft_ms: number | null;
}

export function TraceDrawer({ messageId }: { messageId: string }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<{ retrieval: Trace | null; llm_calls: Call[] } | null>(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !data) {
      try {
        setData(await api.trace<{ retrieval: Trace | null; llm_calls: Call[] }>(messageId));
      } catch {
        // A trace that will not load is a missing explanation, not a broken
        // answer. The answer above it stays exactly as it was.
        setData({ retrieval: null, llm_calls: [] });
      }
    }
  };

  const calls = data?.llm_calls ?? [];
  const trace = data?.retrieval;

  return (
    <div className="text-micro">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex items-center gap-1 rounded-control text-ink-2 hover:text-ink"
      >
        <ChevronRight
          size={12}
          aria-hidden
          className={`transition-transform duration-(--duration-hover) ${open ? "rotate-90" : ""}`}
        />
        Show trace
      </button>

      {open ? (
        <div className="mt-2 space-y-2 rounded-card border border-hairline bg-muted p-2 font-mono">
          {trace ? (
            <>
              <Row label="resolved" value={trace.resolved_from || "—"} />
              {trace.expanded_query && trace.expanded_query !== trace.query ? (
                <Row label="expanded" value={trace.expanded_query} />
              ) : null}
              <Row
                label="dense"
                value={trace.dense_hits
                  .slice(0, 5)
                  .map((h) => `#${h.rank}:${h.score.toFixed(2)}`)
                  .join("  ")}
              />
              <Row
                label="lexical"
                value={
                  trace.lexical_hits.length
                    ? trace.lexical_hits
                        .slice(0, 5)
                        .map((h) => `#${h.rank}:${h.score.toFixed(2)}`)
                        .join("  ")
                    : "no matches"
                }
              />
              <Row label="selected" value={`${trace.selected_chunk_ids.length} chunks`} />
              <Row label="anchors" value={trace.anchors_applied.length ? "applied" : "none"} />
              <Row label="quota" value={trace.quota_applied ? "applied" : "single job"} />
              <Row label="context" value={`${trace.context_chars} chars`} />
              <Row
                label="timings"
                value={Object.entries(trace.timings_ms)
                  .map(([stage, ms]) => `${stage} ${ms}ms`)
                  .join("  ")}
              />
            </>
          ) : (
            <p className="text-ink-2">No trace recorded for this message.</p>
          )}

          {calls.map((call, i) => (
            <Row
              key={i}
              label="llm"
              value={`${call.model} · ${call.input_tokens} in / ${call.output_tokens} out · $${call.cost_usd} · ${call.latency_ms}ms${
                call.ttft_ms !== null ? ` · ttft ${call.ttft_ms}ms` : ""
              }`}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <p className="flex gap-2">
      <span className="w-16 shrink-0 text-ink-2">{label}</span>
      <span className="min-w-0 break-words text-ink">{value}</span>
    </p>
  );
}
