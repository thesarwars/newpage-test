"use client";

/**
 * Drag-and-drop, with a real button next to it.
 *
 * The drop target is never the only way in. A dropzone alone is unusable by
 * keyboard, unusable on touch, and undiscoverable to anyone who does not think
 * to try dragging — so the visible control is a `<button>` driving a
 * visually-hidden `<input type="file">`, and the drop handling is an
 * enhancement layered on top of it.
 *
 * `dragLooksAcceptable` is deliberately permissive: during a drag the browser
 * exposes the MIME type but withholds the filename, so a `.md` file often
 * arrives typeless. Refusing a file that was actually fine is worse than a
 * "release to add" that resolves into a specific rejection a moment later.
 */

import { Upload } from "lucide-react";
import { useRef, useState } from "react";

import { MAX_PAGES } from "@/lib/constants";
import { dragLooksAcceptable } from "@/lib/upload";

interface Props {
  disabled: boolean;
  disabledReason?: string;
  onFiles: (files: File[]) => void;
  onPaste: () => void;
}

export function Dropzone({ disabled, disabledReason, onFiles, onPaste }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState<"no" | "yes" | "reject">("no");
  // dragenter/dragleave fire for every child element the pointer crosses, so a
  // naive boolean flickers as the cursor moves over the text inside the zone.
  // Counting entries against leaves is the standard fix.
  const depth = useRef(0);

  return (
    <div
      onDragEnter={(event) => {
        event.preventDefault();
        depth.current += 1;
        if (disabled) return;
        setDragging(dragLooksAcceptable(event.dataTransfer?.items ?? null) ? "yes" : "reject");
      }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={() => {
        depth.current -= 1;
        if (depth.current <= 0) {
          depth.current = 0;
          setDragging("no");
        }
      }}
      onDrop={(event) => {
        event.preventDefault();
        depth.current = 0;
        setDragging("no");
        if (disabled) return;
        const files = Array.from(event.dataTransfer?.files ?? []);
        if (files.length) onFiles(files);
      }}
      className={`rounded-card border border-dashed p-4 text-center transition-colors duration-(--duration-hover) ${
        disabled
          ? "border-hairline opacity-60"
          : dragging === "yes"
            ? "border-accent bg-muted"
            : dragging === "reject"
              ? "border-gap bg-muted"
              : "border-hairline-strong"
      }`}
    >
      <Upload size={16} className="mx-auto text-ink-2" aria-hidden />

      <p className="mt-2 text-meta text-ink-2">
        {disabled
          ? disabledReason
          : dragging === "reject"
            ? "That file type isn't supported"
            : dragging === "yes"
              ? "Release to add"
              : "Drop a résumé or job description"}
      </p>

      {!disabled && dragging === "no" ? (
        <p className="mt-1 text-micro text-ink-2">
          PDF, DOCX, TXT or MD · up to 10 MB · up to {MAX_PAGES} pages
        </p>
      ) : null}

      <div className="mt-3 flex justify-center gap-2">
        <button
          type="button"
          disabled={disabled}
          onClick={() => input.current?.click()}
          className="rounded-control border border-hairline-strong px-2.5 py-1 text-meta transition-colors duration-(--duration-hover) hover:bg-muted disabled:opacity-50"
        >
          Choose a file
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={onPaste}
          className="rounded-control px-2.5 py-1 text-meta text-accent-ink underline-offset-2 hover:underline disabled:opacity-50"
        >
          Paste text
        </button>
      </div>

      <input
        ref={input}
        type="file"
        multiple
        accept=".pdf,.docx,.txt,.md"
        className="sr-only"
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length) onFiles(files);
          // Reset, or choosing the same file twice in a row fires no change
          // event and the second attempt appears to do nothing.
          event.target.value = "";
        }}
      />
    </div>
  );
}
