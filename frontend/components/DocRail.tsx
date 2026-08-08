"use client";

/**
 * The document rail: what is in the workspace, and how to change it.
 *
 * The uploading state is honest about something the backend cannot hide.
 * Ingest is synchronous and wrapped in a single transaction — parse, chunk and
 * embed all happen inside one request — so there is no observable progress
 * between "sent" and "done". A determinate progress bar here would be a lie
 * that fills to 100% and then sits there, which is worse than saying so.
 */

import { Download, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { MAX_JOBS, SLOW_UPLOAD_MS } from "@/lib/constants";
import type { Document } from "@/lib/types";
import { rejectionFor } from "@/lib/upload";

import { ConfirmDialog } from "./ConfirmDialog";
import { DocCard } from "./DocCard";
import { Dropzone } from "./Dropzone";
import { PasteSheet } from "./PasteSheet";

interface Failure {
  message: string;
  hint?: string;
  offerPaste?: boolean;
}

interface Props {
  documents: Document[];
  canSeedDemo: boolean;
  onAdded: (document: Document) => void;
  onSeeded: (documents: Document[]) => void;
  onRemoved: (id: string) => Promise<void>;
  onDeletedEverything: () => Promise<void>;
}

export function DocRail({
  documents,
  canSeedDemo,
  onAdded,
  onSeeded,
  onRemoved,
  onDeletedEverything,
}: Props) {
  const resume = documents.find((d) => d.kind === "resume");
  const jobs = documents.filter((d) => d.kind === "job");

  // `slow` lives inside the busy state rather than beside it: as separate
  // state, clearing it needs a synchronous setState in the effect body, which
  // is a cascading render. As one value, the timer is the only writer.
  const [busy, setBusy] = useState<{ label: string; slow: boolean } | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pendingRemoval, setPendingRemoval] = useState<Document | null>(null);
  const [deleteAllOpen, setDeleteAllOpen] = useState(false);
  const [focused, setFocused] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);

  // "Still working" is a timer, not a signal — ingest is one synchronous
  // request and there is genuinely nothing to observe between sent and done.
  const activeLabel = busy?.label ?? null;
  useEffect(() => {
    if (!activeLabel) return;
    const timer = setTimeout(
      () => setBusy((current) => (current ? { ...current, slow: true } : current)),
      SLOW_UPLOAD_MS,
    );
    return () => clearTimeout(timer);
  }, [activeLabel]);

  async function run(label: string, work: () => Promise<void>) {
      setBusy({ label, slow: false });
      setFailure(null);
      try {
        await work();
      } catch (error) {
        if (error instanceof ApiError) {
          // Rendered verbatim. The server wrote this copy on purpose — it knows
          // why the request failed, so it also knows what to suggest, and
          // rewriting it here would be guessing at a cause it already named.
          setFailure({
            message: error.message,
            hint: error.hint,
            offerPaste: PASTE_WORTHY.has(error.code),
          });
        } else {
          setFailure({
            message: "Can't reach the API.",
            hint: "Nothing has been lost — try again when the server is back.",
          });
        }
      } finally {
        setBusy(null);
      }
  }

  function uploadFiles(files: File[]) {
    return run(files.length > 1 ? `${files.length} files` : files[0].name, async () => {
        for (const file of files) {
          // Client-side rejection first, so an 11 MB file is not transferred in
          // order to be told it is 11 MB.
          const rejection = rejectionFor(file);
          if (rejection) {
            setFailure({
              message: rejection.message,
              hint: rejection.hint,
              offerPaste: rejection.offerPaste,
            });
            return;
          }
          const kind = !resume && files.length === 1 ? "resume" : "job";
          onAdded(await api.upload(file, kind));
        }
    });
  }

  const jobsFull = jobs.length >= MAX_JOBS;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-3">
      <section>
        <h2 className="px-1 text-micro font-medium uppercase tracking-wide text-ink-2">
          Résumé
        </h2>
        <ul ref={listRef} className="mt-1.5 space-y-1.5">
          {resume ? (
            <DocCard
              document={resume}
              onRemove={setPendingRemoval}
              tabIndex={focused === 0 ? 0 : -1}
              onFocus={() => setFocused(0)}
            />
          ) : (
            <li className="rounded-card border border-dashed border-hairline px-3 py-2 text-meta text-ink-2">
              Not added yet
            </li>
          )}
        </ul>
      </section>

      <section>
        <h2 className="px-1 text-micro font-medium uppercase tracking-wide text-ink-2">
          Jobs (<span data-numeric>{jobs.length}</span> of {MAX_JOBS})
        </h2>
        <ul
          className="mt-1.5 space-y-1.5"
          // Roving tabindex: the rail is one Tab stop and arrows move inside
          // it, so a keyboard user reaching the main content does not have to
          // pass through every document first.
          onKeyDown={(event) => {
            if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
            event.preventDefault();
            const step = event.key === "ArrowDown" ? 1 : -1;
            const next = Math.min(
              Math.max(focused + step, 0),
              documents.length - 1,
            );
            setFocused(next);
            const items = listRef.current?.parentElement?.parentElement?.querySelectorAll("li[tabindex]");
            (items?.[next] as HTMLElement | undefined)?.focus();
          }}
        >
          {jobs.length === 0 ? (
            <li className="rounded-card border border-dashed border-hairline px-3 py-2 text-meta text-ink-2">
              Nothing added yet
            </li>
          ) : (
            jobs.map((document, index) => (
              <DocCard
                key={document.id}
                document={document}
                onRemove={setPendingRemoval}
                tabIndex={focused === index + 1 ? 0 : -1}
                onFocus={() => setFocused(index + 1)}
              />
            ))
          )}
        </ul>
      </section>

      <Dropzone
        disabled={busy !== null || (jobsFull && Boolean(resume))}
        disabledReason={
          busy
            ? undefined
            : `You've reached ${MAX_JOBS} job descriptions. Remove one to add another.`
        }
        onFiles={uploadFiles}
        onPaste={() => setPasteOpen(true)}
      />

      {/* One live region for the whole rail. Screen readers announce these in
          order; several competing regions interrupt each other. */}
      <div aria-live="polite" className="min-h-0">
        {busy ? (
          <p className="rounded-control bg-muted p-2 text-micro text-ink-2">
            Reading {busy.label} — parsing, chunking and embedding happen in one
            step.
            {busy.slow ? " Still working; a long document can take a minute." : ""}
          </p>
        ) : null}

        {failure ? (
          <div className="rounded-control border border-hairline bg-muted p-2 text-micro">
            <p className="text-ink">{failure.message}</p>
            {failure.hint ? <p className="mt-0.5 text-ink-2">{failure.hint}</p> : null}
            {failure.offerPaste ? (
              <button
                type="button"
                onClick={() => {
                  setFailure(null);
                  setPasteOpen(true);
                }}
                className="mt-1 text-accent-ink underline-offset-2 hover:underline"
              >
                Paste text instead
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="mt-auto space-y-1.5 pt-2">
        {canSeedDemo ? (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() =>
              run("the demo corpus", async () => {
                const { documents: seeded } = await api.seedDemo();
                onSeeded(seeded);
              })
            }
            className="flex w-full items-center justify-center gap-1.5 rounded-control bg-accent-fill px-3 py-2 text-body text-white transition-opacity duration-(--duration-hover) hover:opacity-90 disabled:opacity-50"
          >
            <Download size={14} aria-hidden />
            Load demo data
          </button>
        ) : null}

        {documents.length > 0 ? (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => setDeleteAllOpen(true)}
            className="flex w-full items-center justify-center gap-1.5 rounded-control border border-hairline px-3 py-2 text-meta text-ink-2 transition-colors duration-(--duration-hover) hover:border-gap hover:text-gap disabled:opacity-50"
          >
            <Trash2 size={13} aria-hidden />
            Delete everything
          </button>
        ) : null}
      </div>

      <PasteSheet
        open={pasteOpen}
        busy={busy !== null}
        defaultKind={resume ? "job" : "resume"}
        canAddResume={!resume}
        onCancel={() => setPasteOpen(false)}
        onSubmit={(text, kind, label) => {
          setPasteOpen(false);
          void run("pasted text", async () => {
            onAdded(await api.paste(text, kind, label));
          });
        }}
      />

      <ConfirmDialog
        open={pendingRemoval !== null}
        title={`Remove "${pendingRemoval?.label ?? ""}"?`}
        body="Its passages and extracted requirements go with it. Job numbers after it shift up."
        confirmLabel="Remove"
        destructive
        busy={busy !== null}
        onCancel={() => setPendingRemoval(null)}
        onConfirm={() => {
          const target = pendingRemoval;
          setPendingRemoval(null);
          if (target) void run(target.label, () => onRemoved(target.id));
        }}
      />

      <ConfirmDialog
        open={deleteAllOpen}
        title="Delete everything?"
        body="Your documents, their passages and this conversation are removed from the server immediately. This cannot be undone."
        confirmLabel="Delete everything"
        destructive
        busy={busy !== null}
        onCancel={() => setDeleteAllOpen(false)}
        onConfirm={() => {
          setDeleteAllOpen(false);
          void run("deletion", onDeletedEverything);
        }}
      />
    </div>
  );
}

/**
 * Failures whose fix is genuinely "paste it instead".
 *
 * Not every error: offering the paste sheet after `too_many_jobs` would be
 * suggesting the user do the exact thing that just hit the limit.
 */
const PASTE_WORTHY = new Set([
  "no_text_layer",
  "encrypted_pdf",
  "too_large",
  "too_many_pages",
  "unsupported_type",
  "parse_failed",
]);
