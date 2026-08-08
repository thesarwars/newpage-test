/**
 * The conversation state machine.
 *
 * A pure reducer over the SSE event stream, deliberately separated from the
 * fetch plumbing so it can be tested against recorded streams in node with no
 * DOM and no network. Streaming UIs are usually where correctness goes to die
 * precisely because the logic is tangled into an effect; this is the part worth
 * being able to replay.
 */

import type { Citation } from "./types";

export type Phase = "idle" | "resolving" | "retrieving" | "generating" | "done";

export interface SourceChunk {
  id: string;
  doc_id: string;
  doc_label: string;
  kind: string;
  section: string;
  preview: string;
  char_start: number;
  char_end: number;
}

export interface Grounding {
  citations: number;
  max_score: number;
  low_evidence: boolean;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cost_usd: string;
}

export interface Refusal {
  reason: string;
  message: string;
  suggestion?: string;
}

export interface StreamFailure {
  code: string;
  message: string;
  hint?: string;
  retry_after?: number | null;
}

export interface NoContext {
  reason: string;
  suggestions: string[];
}

/** The assistant turn currently being produced, or the last one produced. */
export interface Turn {
  messageId: string | null;
  phase: Phase;
  detail: string;
  /** Raw markdown as it accumulates. Citation offsets index into this. */
  text: string;
  citations: Citation[];
  sources: SourceChunk[];
  scope: { job_ids: string[]; intent: string; resolved_from: string } | null;
  demoMode: boolean;
  grounding: Grounding | null;
  usage: Usage | null;
  latencyMs: number | null;
  ttftMs: number | null;
  refusal: Refusal | null;
  noContext: NoContext | null;
  failure: StreamFailure | null;
  /** True once the server said it was finished, or the stream died. */
  finished: boolean;
  /** The user aborted. Partial text is kept and labelled, never discarded. */
  stopped: boolean;
}

export const emptyTurn: Turn = {
  messageId: null,
  phase: "idle",
  detail: "",
  text: "",
  citations: [],
  sources: [],
  scope: null,
  demoMode: false,
  grounding: null,
  usage: null,
  latencyMs: null,
  ttftMs: null,
  refusal: null,
  noContext: null,
  failure: null,
  finished: false,
  stopped: false,
};

export type Action =
  | { type: "start" }
  | { type: "frame"; event: string; data: Record<string, unknown> }
  | { type: "stopped" }
  | { type: "failed"; failure: StreamFailure };

export function reduce(turn: Turn, action: Action): Turn {
  switch (action.type) {
    case "start":
      return { ...emptyTurn, phase: "resolving" };

    case "stopped":
      // The partial answer is kept. A user who stops a stream wants to read
      // what arrived; replacing it with nothing punishes them for stopping.
      return { ...turn, stopped: true, finished: true, phase: "done" };

    case "failed":
      return { ...turn, failure: action.failure, finished: true, phase: "done" };

    case "frame":
      return applyFrame(turn, action.event, action.data);
  }
}

function applyFrame(turn: Turn, event: string, data: Record<string, unknown>): Turn {
  switch (event) {
    case "status":
      return {
        ...turn,
        phase: (data.phase as Phase) ?? turn.phase,
        detail: (data.detail as string) ?? "",
      };

    case "scope":
      return {
        ...turn,
        phase: "retrieving",
        scope: {
          job_ids: (data.job_ids as string[]) ?? [],
          intent: (data.intent as string) ?? "",
          resolved_from: (data.resolved_from as string) ?? "",
        },
        demoMode: Boolean(data.demo_mode),
      };

    case "sources":
      return { ...turn, sources: (data.chunks as SourceChunk[]) ?? [] };

    case "delta":
      return {
        ...turn,
        phase: "generating",
        text: turn.text + ((data.text as string) ?? ""),
      };

    case "citation": {
      const citation = data as unknown as Citation;
      // The server already de-duplicates by span and reuses the index. This
      // guards the client against a repeat anyway, because rendering two marks
      // with the same number is worse than dropping one.
      if (turn.citations.some((c) => c.index === citation.index)) return turn;
      return { ...turn, citations: [...turn.citations, citation] };
    }

    case "refusal":
      // Partial text is discarded, not kept. On a streaming path the refusal
      // necessarily arrives after some text is already on the wire — that
      // ordering is not avoidable — so the client throws the fragment away
      // rather than leaving an orphaned half-answer above the refusal card.
      return {
        ...turn,
        text: "",
        citations: [],
        refusal: {
          reason: (data.reason as string) ?? "model_refusal",
          message: (data.message as string) ?? "",
          suggestion: data.suggestion as string | undefined,
        },
      };

    case "no_context":
      return {
        ...turn,
        noContext: {
          reason: (data.reason as string) ?? "",
          suggestions: (data.suggestions as string[]) ?? [],
        },
      };

    case "error":
      return {
        ...turn,
        failure: {
          code: (data.code as string) ?? "upstream_error",
          message: (data.message as string) ?? "Something went wrong.",
          hint: data.hint as string | undefined,
          retry_after: (data.retry_after as number | null) ?? null,
        },
        finished: true,
        phase: "done",
      };

    case "done":
      return {
        ...turn,
        messageId: (data.message_id as string) ?? turn.messageId,
        grounding: (data.grounding as Grounding) ?? null,
        usage: (data.usage as Usage) ?? null,
        latencyMs: (data.latency_ms as number) ?? null,
        ttftMs: (data.ttft_ms as number) ?? null,
        finished: true,
        phase: "done",
      };

    default:
      // An event this client does not know about is not a failure. The server
      // may add one before the client is redeployed.
      return turn;
  }
}

/**
 * What to render for a finished turn, as one decision rather than several
 * scattered ternaries in the view.
 */
export type Outcome =
  | { kind: "streaming" }
  | { kind: "answer" }
  | { kind: "refusal"; refusal: Refusal }
  | { kind: "no_context"; noContext: NoContext }
  | { kind: "error"; failure: StreamFailure }
  | { kind: "stopped" }
  | { kind: "empty" };

export function outcomeOf(turn: Turn): Outcome {
  if (turn.failure) return { kind: "error", failure: turn.failure };
  if (turn.refusal) return { kind: "refusal", refusal: turn.refusal };
  if (turn.noContext) return { kind: "no_context", noContext: turn.noContext };
  if (turn.stopped && turn.text) return { kind: "stopped" };
  if (!turn.finished && turn.phase !== "idle") return { kind: "streaming" };
  if (turn.text) return { kind: "answer" };
  return { kind: "empty" };
}
