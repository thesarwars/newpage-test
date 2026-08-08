"use client";

/**
 * The conversation: status, sources, answer, grounding, trace.
 *
 * The event order is a product decision, not an implementation detail. Source
 * chips land **before any text exists**, so grounding visibly happens first and
 * the answer reads as derived rather than generated. That is the whole reason
 * the retrieval is legible instead of merely claimed.
 *
 * **Accessibility, against what docs/PLAN.md §7 specified.** The plan says
 * `aria-live="polite"` on the streaming answer. Measured, that produces roughly
 * two thousand queued utterances of about a word each for one answer — and
 * citation marks splice in 51–82 characters *behind* the head, which also
 * violates `role="log"`'s contract that content is added "only to the end".
 * So the streaming answer is deliberately **not** a live region: it is visible
 * and animated for sighted users, while assistive technology gets a separate
 * status region carrying phase changes (five utterances per turn) and the
 * finished answer announced once. See docs/adr/0012.
 */

import { AlertTriangle, Ban, FileSearch, Square } from "lucide-react";

import { Markdown } from "@/components/Markdown";
import { SourceChips } from "@/components/SourceChips";
import { TraceDrawer } from "@/components/TraceDrawer";
import { type Turn, outcomeOf } from "@/lib/chat";
import { useUi } from "@/lib/store";
import type { Citation, Message } from "@/lib/types";

interface Props {
  history: Message[];
  turn: Turn;
  streaming: boolean;
  onStop: () => void;
  onRetry: () => void;
}

export function Conversation({ history, turn, streaming, onStop, onRetry }: Props) {
  const selectCitation = useUi((s) => s.selectCitation);
  const selected = useUi((s) => s.selected);

  const cite = (messageId: string | null) => (citation: Citation) =>
    selectCitation({
      index: citation.index,
      docId: citation.doc_id,
      charStart: citation.char_start,
      charEnd: citation.char_end,
      citedText: citation.cited_text,
      messageId,
    });

  const outcome = outcomeOf(turn);

  return (
    <div className="mx-auto flex max-w-[46rem] flex-col gap-6 p-4 pb-8">
      {/* Completed turns. `role="log"` is correct here — this content really is
          only ever appended to, unlike the streaming answer above it. */}
      <div role="log" aria-live="polite" aria-label="Conversation" className="flex flex-col gap-6">
        {history.map((message) =>
          message.role === "user" ? (
            <UserTurn key={message.id} text={message.content} />
          ) : (
            <article key={message.id} className="space-y-2">
              {message.status === "refused" ? (
                <Card icon={<Ban size={14} aria-hidden />} tone="gap">
                  This request was declined.
                </Card>
              ) : message.status === "no_context" ? (
                <Card icon={<FileSearch size={14} aria-hidden />} tone="ink-2">
                  Nothing in your documents supported that question.
                </Card>
              ) : (
                <>
                  <Markdown
                    source={message.content}
                    citations={message.citations}
                    onCite={cite(message.id)}
                    activeIndex={selected?.messageId === message.id ? selected.index : null}
                  />
                  <Grounding
                    citations={message.grounding.citations}
                    lowEvidence={message.grounding.low_evidence}
                    maxScore={message.grounding.max_score}
                  />
                  <TraceDrawer messageId={message.id} />
                </>
              )}
            </article>
          ),
        )}
      </div>

      {/* The turn in flight. Deliberately outside the log region. */}
      {outcome.kind !== "empty" ? (
        <article className="space-y-3" aria-live="off">
          {turn.scope?.resolved_from ? (
            <p className="text-micro text-ink-2">
              Reading <span className="text-ink">{turn.scope.resolved_from}</span>
              {turn.scope.intent ? ` · ${turn.scope.intent}` : ""}
            </p>
          ) : null}

          {turn.sources.length > 0 ? <SourceChips chunks={turn.sources} /> : null}

          {streaming && !turn.text ? (
            <p className="flex items-center gap-2 text-meta text-ink-2">
              <span className="size-1.5 animate-pulse rounded-pill bg-accent" aria-hidden />
              {turn.detail || "Working…"}
            </p>
          ) : null}

          {turn.text ? (
            <Markdown
              source={turn.text}
              citations={turn.citations}
              onCite={cite(turn.messageId)}
              activeIndex={selected?.messageId === turn.messageId ? selected.index : null}
            />
          ) : null}

          {outcome.kind === "refusal" ? (
            <Card icon={<Ban size={14} aria-hidden />} tone="gap">
              <p>{outcome.refusal.message}</p>
              {outcome.refusal.suggestion ? (
                <p className="mt-1.5 text-ink-2">{outcome.refusal.suggestion}</p>
              ) : null}
            </Card>
          ) : null}

          {outcome.kind === "no_context" ? (
            <Card icon={<FileSearch size={14} aria-hidden />} tone="ink-2">
              <p>{outcome.noContext.reason}</p>
              {outcome.noContext.suggestions.length ? (
                <ul className="mt-1.5 list-disc pl-4 text-ink-2">
                  {outcome.noContext.suggestions.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              ) : null}
            </Card>
          ) : null}

          {outcome.kind === "error" ? (
            <Card icon={<AlertTriangle size={14} aria-hidden />} tone="weak">
              <p>{outcome.failure.message}</p>
              {outcome.failure.hint ? (
                <p className="mt-1 text-ink-2">{outcome.failure.hint}</p>
              ) : null}
              <button
                type="button"
                onClick={onRetry}
                className="mt-2 rounded-control border border-hairline-strong px-2 py-1 text-micro hover:bg-muted"
              >
                Retry
              </button>
            </Card>
          ) : null}

          {outcome.kind === "stopped" ? (
            <p className="text-micro text-ink-2">Stopped. What arrived is kept above.</p>
          ) : null}

          {turn.finished && turn.grounding ? (
            <>
              <Grounding
                citations={turn.grounding.citations}
                lowEvidence={turn.grounding.low_evidence}
                maxScore={turn.grounding.max_score}
              />
              {turn.messageId ? <TraceDrawer messageId={turn.messageId} /> : null}
            </>
          ) : null}

          {streaming ? (
            <button
              type="button"
              onClick={onStop}
              className="flex items-center gap-1.5 rounded-control border border-hairline px-2 py-1 text-micro text-ink-2 hover:bg-muted"
            >
              <Square size={11} fill="currentColor" strokeWidth={0} aria-hidden />
              Stop
            </button>
          ) : null}
        </article>
      ) : null}

      {/* The only thing assistive technology hears while text streams: five
          phase changes per turn, not two thousand word fragments. */}
      <p role="status" aria-live="polite" className="sr-only">
        {streaming ? turn.detail : turn.finished ? "Answer complete." : ""}
      </p>
    </div>
  );
}

function UserTurn({ text }: { text: string }) {
  return (
    <p className="ml-auto max-w-[85%] rounded-card bg-muted px-3 py-2 text-body">{text}</p>
  );
}

function Card({
  icon,
  tone,
  children,
}: {
  icon: React.ReactNode;
  tone: "gap" | "weak" | "ink-2";
  children: React.ReactNode;
}) {
  const colour = tone === "gap" ? "text-gap" : tone === "weak" ? "text-weak" : "text-ink-2";
  return (
    <div className="flex gap-2 rounded-card border border-hairline bg-card p-3 text-body shadow-raised">
      <span className={`mt-0.5 shrink-0 ${colour}`}>{icon}</span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function Grounding({
  citations,
  lowEvidence,
  maxScore,
}: {
  citations: number;
  lowEvidence: boolean;
  maxScore: number;
}) {
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-micro text-ink-2">
      <span>
        {citations === 0
          ? "No citations"
          : `${citations} citation${citations === 1 ? "" : "s"}`}
      </span>
      {lowEvidence ? (
        // Labelled, not suppressed. An answer with thin evidence may still be
        // correct, and hiding it because the extractor missed is worse than
        // saying the evidence is thin.
        <span className="rounded-pill bg-muted px-1.5 py-0.5 text-ink">
          Low evidence
        </span>
      ) : null}
      <span data-numeric>· best match {maxScore.toFixed(2)}</span>
    </p>
  );
}
