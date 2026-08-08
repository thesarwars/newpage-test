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
 *
 * The centre pane is M6's honest placeholder: the shell, the rail, upload,
 * demo seeding and deletion are real; the conversation is M7.
 */

import { MessageSquare } from "lucide-react";

import { DocRail } from "@/components/DocRail";
import { ThemeToggle } from "@/components/ThemeToggle";
import { WorkspaceShell } from "@/components/WorkspaceShell";
import { api } from "@/lib/api";
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

  if (state === "loading") {
    return (
      <div className="grid h-dvh place-items-center">
        <p className="sr-only" role="status">
          Loading your workspace.
        </p>
        <div
          aria-hidden
          className="h-1 w-40 animate-pulse rounded-pill bg-muted"
        />
      </div>
    );
  }

  if (state === "error" || !workspace) {
    return (
      <div className="grid h-dvh place-items-center p-6">
        <div className="max-w-sm text-center">
          <h1 className="text-title font-semibold">{failure?.message}</h1>
          {failure?.hint ? (
            <p className="mt-2 text-body text-ink-2">{failure.hint}</p>
          ) : null}
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

  return (
    <WorkspaceShell
      banner={
        <header className="flex items-center justify-between gap-3 border-b border-hairline px-3 py-2">
          <div className="flex min-w-0 items-center gap-3">
            <span className="text-meta font-semibold">Career Intelligence</span>
            {workspace.demo_mode ? (
              /* Persistent, not dismissible. Free-text generation is stubbed,
                 and a reviewer must never mistake a stub for model output —
                 the alternative is them concluding the model is poor. */
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
          }}
        />
      }
    >
      <div className="grid h-full place-items-center p-8">
        <div className="max-w-md text-center">
          <MessageSquare
            size={20}
            className="mx-auto text-ink-2"
            aria-hidden
          />
          <h1 className="mt-3 text-section font-semibold">
            {workspace.documents.length === 0
              ? "Add a résumé and a job description"
              : "Ready to answer questions"}
          </h1>
          <p className="mt-2 text-body text-ink-2">
            {workspace.documents.length === 0
              ? "Or load the demo corpus from the rail — a résumé and three postings, spread across the fit range."
              : `${workspace.documents.length} documents indexed. The conversation lands in the next milestone; retrieval, citations and the evidence panel are already working over these documents via the API.`}
          </p>
        </div>
      </div>
    </WorkspaceShell>
  );
}
