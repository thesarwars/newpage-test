"use client";

/**
 * The streaming half of the conversation.
 *
 * Thin on purpose: the parsing lives in `sse.ts` and the state machine in
 * `chat.ts`, both pure and both replayable against recorded byte streams. What
 * is left here is the part that genuinely needs a browser — `fetch`, a
 * `ReadableStream`, and an `AbortController`.
 *
 * One transport fact shapes the whole error path. The response carries **no
 * `Content-Length` and no chunked framing**, so a clean end and a dropped
 * connection are byte-identical to `fetch`: the reader simply finishes. The
 * client therefore cannot treat "reader done" as success. Only a `done` event
 * means the answer completed; anything else is a truncation, and the partial
 * text is kept with a Retry rather than silently presented as an answer.
 */

import { useCallback, useRef, useState } from "react";

import { API_BASE } from "./api";
import { type Action, type Turn, emptyTurn, reduce } from "./chat";
import { SseParser, payloadOf } from "./sse";

export interface AskOptions {
  jobIds?: string[];
  mode?: "analysis" | "interview";
}

export function useChatStream(onFinished?: (turn: Turn) => void) {
  const [turn, setTurn] = useState<Turn>(emptyTurn);
  const controller = useRef<AbortController | null>(null);
  // The reducer runs against a ref as well as state: several frames can arrive
  // between renders, and `setTurn(prev => …)` alone would make the `done`
  // handler's view of the turn a render behind.
  const current = useRef<Turn>(emptyTurn);

  const apply = useCallback((action: Action) => {
    current.current = reduce(current.current, action);
    setTurn(current.current);
  }, []);

  const stop = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
  }, []);

  const ask = useCallback(
    async (message: string, options: AskOptions = {}) => {
      stop();
      const abort = new AbortController();
      controller.current = abort;
      apply({ type: "start" });

      let sawDone = false;

      try {
        const response = await fetch(`${API_BASE}/chat/`, {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            // `text/event-stream` alone used to 406 — DRF had no renderer for
            // it. Fixed server-side, and `*/*` is kept as a fallback so an older
            // deployment degrades rather than failing outright.
            Accept: "text/event-stream, */*",
          },
          body: JSON.stringify({
            message,
            scope: { job_ids: options.jobIds ?? [], mode: options.mode ?? "analysis" },
          }),
          signal: abort.signal,
        });

        if (!response.ok || !response.body) {
          const body = await response.json().catch(() => null);
          apply({
            type: "failed",
            failure: {
              code: body?.error_code ?? "upstream_error",
              message: body?.message ?? "The request failed before the answer started.",
              hint: body?.hint,
              retry_after: body?.retry_after ?? null,
            },
          });
          return;
        }

        const reader = response.body.getReader();
        const parser = new SseParser();

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const frame of parser.push(value)) {
            const data = payloadOf<Record<string, unknown>>(frame);
            if (!data) continue;
            if (frame.event === "done") sawDone = true;
            apply({ type: "frame", event: frame.event, data });
          }
        }

        for (const frame of parser.end()) {
          const data = payloadOf<Record<string, unknown>>(frame);
          if (!data) continue;
          if (frame.event === "done") sawDone = true;
          apply({ type: "frame", event: frame.event, data });
        }

        if (!sawDone && !current.current.finished) {
          // The stream ended without saying so. Keep whatever arrived — a user
          // reading a half-answer wants to keep reading it — and offer Retry.
          apply({
            type: "failed",
            failure: {
              code: "stream_truncated",
              message: "The answer stopped early.",
              hint: "The connection dropped mid-answer. What arrived is kept above.",
            },
          });
        }
      } catch (error) {
        if ((error as Error).name === "AbortError") {
          apply({ type: "stopped" });
        } else {
          apply({
            type: "failed",
            failure: {
              code: "offline",
              message: "Lost the connection to the API.",
              hint: "Nothing has been lost — try again when the server is back.",
            },
          });
        }
      } finally {
        controller.current = null;
        onFinished?.(current.current);
      }
    },
    [apply, stop, onFinished],
  );

  const reset = useCallback(() => {
    current.current = emptyTurn;
    setTurn(emptyTurn);
  }, []);

  return { turn, ask, stop, reset, streaming: !turn.finished && turn.phase !== "idle" };
}
