"use client";

/**
 * A destructive confirmation, on the native `<dialog>` element.
 *
 * `showModal()` gives four things that are otherwise hand-rolled and usually
 * hand-rolled wrong: a focus trap, `aria-modal` semantics, Escape-to-close, and
 * focus restored to whatever opened it. ADR-0009 declines a component library
 * partly on this basis — the dialog was the only piece with real accessibility
 * depth, and the platform already has it.
 *
 * Initial focus goes to **Cancel**, never the destructive action. Someone who
 * hits Enter reflexively should not have deleted their workspace.
 */

import { useEffect, useRef } from "react";

interface Props {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  destructive = false,
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      cancelRef.current?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      // alertdialog rather than dialog: this interrupts to confirm something
      // irreversible, and screen readers announce the two differently.
      role="alertdialog"
      aria-labelledby="confirm-title"
      aria-describedby="confirm-body"
      // Escape fires `cancel` before `close`; routing it through the same
      // handler keeps React's state and the element's state from diverging.
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
      onClose={() => {
        if (open && !busy) onCancel();
      }}
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-card border border-hairline bg-card p-0 text-ink shadow-raised backdrop:bg-black/40"
    >
      <div className="p-5">
        <h2 id="confirm-title" className="text-title font-semibold">
          {title}
        </h2>
        <p id="confirm-body" className="mt-2 text-body text-ink-2">
          {body}
        </p>

        <div className="mt-5 flex justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-control border border-hairline-strong px-3 py-1.5 text-body transition-colors duration-(--duration-hover) hover:bg-muted disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={`rounded-control px-3 py-1.5 text-body text-white transition-opacity duration-(--duration-hover) disabled:opacity-50 ${
              destructive ? "bg-gap" : "bg-accent-fill"
            }`}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}
