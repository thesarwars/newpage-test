"use client";

/**
 * The workspace.
 *
 * A Client Component from the root down, because the whole of this app's state
 * lives behind an httpOnly cookie set by Django on a *different origin*. A
 * Server Component cannot read it in any real deployment — and in local
 * development it accidentally can, because `localhost:3000` and
 * `localhost:8000` share a cookie jar, which is exactly the kind of accident
 * that works until the first deploy.
 */

import { useCallback, useEffect, useState } from "react";

import { Composer } from "@/components/Composer";
import { Conversation } from "@/components/Conversation";
import { DocRail } from "@/components/DocRail";
import { EvidencePanel } from "@/components/EvidencePanel";
import { ThemeToggle } from "@/components/ThemeToggle";
import { WorkspaceShell } from "@/components/WorkspaceShell";
import { api } from "@/lib/api";
import type { Turn } from "@/lib/chat";
import { useChatStream } from "@/lib/useChatStream";
import { useUi } from "@/lib/store";
import type { Message, Suggestion } from "@/lib/types";
import { useWorkspace } from "@/lib/useWorkspace";

export default function Workspace() {
  const {
    workspace,
    state,
    failure,
    reload,
    setWorkspace,
    addDocument,
    replaceDocuments,
    removeDocument,
  } = useWorkspace();

  const [history, setHistory] = useState<Message[]>([]);
  const [seededFor, setSeededFor] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [lastAsked, setLastAsked] = useState<string | null>(null);
  const scope = useUi((s) => s.scopeJobIds);
  const mode = useUi((s) => s.mode);

  // When a turn finishes, fold it into history and clear the live turn. The
  // server is the source of truth for what was stored — a refusal is persisted
  // with empty content, and reading that back is how the reload view and the
  // live view stay identical.
  const onFinished = useCallback(async (turn: Turn) => {
    if (!turn.messageId) return;
    try {
      const { messages } = await api.messages();
      setHistory(messages);
    } catch {
      // History will re-sync on the next successful turn or reload. The answer
      // the user just read is still on screen either way.
    }
  }, []);

  const { turn, ask, stop, reset, streaming } = useChatStream(onFinished);

  // Seed the transcript from the hydrated workspace, during render rather than
  // in an effect. React's documented pattern for "adjust state when a prop
  // changes"; the effect version is a cascading render, which React 19's
  // compiler lint objects to and is right to. Keyed on the session id, so
  // "delete everything" (which mints a new session) reseeds and an ordinary
  // re-render does not.
  if (workspace && seededFor !== workspace.id) {
    setSeededFor(workspace.id);
    setHistory(workspace.messages);
  }

  useEffect(() => {
    if (state !== "ready") return;
    let cancelled = false;
    void api
      .suggestions(scope, mode)
      .then(({ suggestions: chips }) => !cancelled && setSuggestions(chips))
      .catch(() => !cancelled && setSuggestions([]));
    return () => {
      cancelled = true;
    };
  }, [state, scope, mode, history.length]);

  const send = useCallback(
    (message: string) => {
      setLastAsked(message);
      reset();
      setHistory((current) => [
        ...current,
        {
          id: `local-${Date.now()}`,
          role: "user",
          content: message,
          mode,
          intent: "",
          scope_job_ids: scope,
          status: "complete",
          refusal_reason: "",
          grounding: { max_score: 0, citations: 0, low_evidence: false },
          created_at: new Date().toISOString(),
          citations: [],
        },
      ]);
      void ask(message, { jobIds: scope, mode });
    },
    [ask, reset, scope, mode],
  );

  if (state === "loading") {
    return (
      <div className="grid h-dvh place-items-center">
        <p className="sr-only" role="status">
          Loading your workspace.
        </p>
        <div aria-hidden className="h-1 w-40 animate-pulse rounded-pill bg-muted" />
      </div>
    );
  }

  if (state === "error" || !workspace) {
    return (
      <div className="grid h-dvh place-items-center p-6">
        <div className="max-w-sm text-center">
          <h1 className="text-title font-semibold">{failure?.message}</h1>
          {failure?.hint ? <p className="mt-2 text-body text-ink-2">{failure.hint}</p> : null}
          <button
            type="button"
            onClick={() => void reload()}
            className="mt-4 rounded-control bg-accent-fill px-3 py-1.5 text-body text-white"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }

  const jobs = workspace.documents.filter((d) => d.kind === "job");
  const ready = workspace.documents.length > 0;

  return (
    <WorkspaceShell
      banner={
        <header className="flex items-center justify-between gap-3 border-b border-hairline px-3 py-2">
          <div className="flex min-w-0 items-center gap-3">
            <span className="text-meta font-semibold">Career Intelligence</span>
            {workspace.demo_mode ? (
              /* Persistent, not dismissible. Free-text generation is stubbed,
                 and a reviewer must never mistake a stub for model output. */
              <span className="truncate rounded-pill bg-muted px-2 py-0.5 text-micro text-ink">
                Demo mode — no API key, so answers are assembled from retrieved
                passages rather than generated. Citations are real.
              </span>
            ) : null}
          </div>
          <ThemeToggle />
        </header>
      }
      rail={
        <DocRail
          documents={workspace.documents}
          canSeedDemo={workspace.can_seed_demo}
          onAdded={addDocument}
          onSeeded={replaceDocuments}
          onRemoved={removeDocument}
          onDeletedEverything={async () => {
            setWorkspace(await api.deleteEverything());
            setHistory([]);
            reset();
          }}
        />
      }
      evidence={<EvidencePanel />}
    >
      <div className="flex h-full min-h-0 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          {ready || history.length > 0 ? (
            <Conversation
              history={history}
              turn={turn}
              streaming={streaming}
              onStop={stop}
              onRetry={() => lastAsked && send(lastAsked)}
            />
          ) : (
            <div className="grid h-full place-items-center p-8">
              <div className="max-w-md text-center">
                <h1 className="text-section font-semibold">
                  Add a résumé and a job description
                </h1>
                <p className="mt-2 text-body text-ink-2">
                  Or load the demo corpus from the rail — a résumé and three
                  postings, spread across the fit range.
                </p>
              </div>
            </div>
          )}
        </div>

        {ready ? (
          <Composer
            jobs={jobs}
            suggestions={suggestions}
            disabled={streaming}
            onSend={send}
          />
        ) : null}
      </div>
    </WorkspaceShell>
  );
}
