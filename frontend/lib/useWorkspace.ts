"use client";

/**
 * The workspace: one loader, one reducer, one place that knows how to fail.
 *
 * All state lives client-side. It has to: the session is an httpOnly cookie set
 * by Django on a different origin, and while a Server Component could
 * accidentally read it in local development — cookies are host-scoped, not
 * origin-scoped, so `localhost:8000` and `localhost:3000` share a jar — that is
 * an artifact of both services being on `localhost`. In any real deployment the
 * Next origin never sees it. Building on the accident would produce something
 * that works locally and breaks the first time it is deployed.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, OfflineError, api } from "./api";
import type { Document, Workspace } from "./types";

export type LoadState = "loading" | "ready" | "error";

export interface WorkspaceFailure {
  kind: "offline" | "api";
  message: string;
  hint?: string;
}

function describe(error: unknown): WorkspaceFailure {
  if (error instanceof OfflineError) {
    return {
      kind: "offline",
      // True here and false for every other failure: the request never reached
      // the server, so nothing was half-done.
      message: "Can't reach the API.",
      hint: "The server isn't answering. Nothing has been lost — retry when it's back.",
    };
  }
  if (error instanceof ApiError) {
    return { kind: "api", message: error.message, hint: error.hint };
  }
  return {
    kind: "api",
    message: "Something went wrong.",
    hint: "Reload the page to start again.",
  };
}

export function useWorkspace() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [failure, setFailure] = useState<WorkspaceFailure | null>(null);
  const started = useRef(false);

  const load = useCallback(async () => {
    setState("loading");
    setFailure(null);
    try {
      setWorkspace(await api.bootstrap());
      setState("ready");
    } catch (error) {
      setFailure(describe(error));
      setState("error");
    }
  }, []);

  useEffect(() => {
    // React Strict Mode runs effects twice in development. `POST /sessions/` is
    // idempotent server-side precisely so that is harmless, but the guard also
    // stops the second run from racing the first into `setState`.
    if (started.current) return;
    started.current = true;
    void load();
  }, [load]);

  /** Splice a newly created document in without re-fetching the workspace. */
  const addDocument = useCallback((document: Document) => {
    setWorkspace((current) =>
      current
        ? {
            ...current,
            documents: railOrder([...current.documents, document]),
            can_seed_demo: false,
          }
        : current,
    );
  }, []);

  const replaceDocuments = useCallback((documents: Document[]) => {
    setWorkspace((current) =>
      current
        ? {
            ...current,
            documents: railOrder(documents),
            can_seed_demo: documents.length === 0,
            demo_seeded: current.demo_seeded || documents.length > 0,
          }
        : current,
    );
  }, []);

  const removeDocument = useCallback(async (id: string) => {
    await api.remove(id);
    // Re-fetch rather than splice: deleting a job renumbers every ordinal after
    // it server-side, and "Job #3" becoming "Job #2" is exactly the label the
    // user is about to type into a question. Guessing at the renumbering client
    // side would put the rail and the retriever into different worlds.
    const fresh = await api.workspace();
    setWorkspace(fresh);
  }, []);

  return {
    workspace,
    state,
    failure,
    reload: load,
    setWorkspace,
    addDocument,
    replaceDocuments,
    removeDocument,
  };
}

/**
 * Résumé first, then jobs by ordinal.
 *
 * The server sorts the same way (`apps/documents/models.py::rail_order`); this
 * keeps a locally-spliced document in the same order it will occupy after the
 * next reload, so the rail does not reshuffle when nothing changed.
 */
export function railOrder(documents: Document[]): Document[] {
  return [...documents].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "resume" ? -1 : 1;
    return a.ordinal - b.ordinal;
  });
}
