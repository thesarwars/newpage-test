/**
 * Client-side upload rejection, mirroring the server's rules.
 *
 * Not a duplicate of the server check — the server keeps its own, because a
 * client check is a courtesy and never a control. This exists because the
 * alternative is transferring an 11 MB file across the network in order to be
 * told it is too large, which is the sort of thing that reads as a slow app
 * rather than as a rejected file.
 *
 * The copy is deliberately identical to what the server would have said, so the
 * user cannot tell which layer refused them — and so that when the two rules
 * drift, the difference is visible in this file rather than in the UI.
 */

import { ALLOWED_EXTENSIONS, MAX_FILE_BYTES } from "./constants";

export interface Rejection {
  code: string;
  message: string;
  hint: string;
  /** Whether the "Paste text instead" affordance makes sense for this failure. */
  offerPaste: boolean;
}

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot + 1).toLowerCase();
}

export function rejectionFor(file: File): Rejection | null {
  // Size first, matching the server's order. A 12 MB `.exe` should be told it
  // is too large before it is told the type is wrong, so that fixing one thing
  // does not simply reveal the next complaint.
  if (file.size > MAX_FILE_BYTES) {
    return {
      code: "too_large",
      message: "That file is larger than 10 MB.",
      hint: "Export a smaller PDF, or paste the text instead.",
      offerPaste: true,
    };
  }

  if (!ALLOWED_EXTENSIONS.has(extensionOf(file.name))) {
    return {
      code: "unsupported_type",
      message: "That file type isn't supported.",
      hint: "Upload a PDF, DOCX, TXT or Markdown file, or paste the text.",
      offerPaste: true,
    };
  }

  if (file.size === 0) {
    return {
      code: "parse_failed",
      message: "That file is empty.",
      hint: "Choose a different file, or paste the text.",
      offerPaste: true,
    };
  }

  return null;
}

/**
 * Whether a drag can be accepted, decided from the drag event alone.
 *
 * During a drag the browser exposes `DataTransferItem.type` but *not* the
 * filename — deliberately, so a page cannot inventory what you are hovering
 * over. So this can only check MIME type, and a `.md` file often arrives with
 * an empty type. Unknown types are therefore allowed through and rejected on
 * drop, where the filename is finally readable: a false "supported" that
 * resolves in a moment is much better than refusing a file that was fine.
 */
const DRAG_TYPES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "text/plain",
  "text/markdown",
  "",
]);

export function dragLooksAcceptable(items: DataTransferItemList | null): boolean {
  if (!items || items.length === 0) return true;
  return Array.from(items).every(
    (item) => item.kind !== "file" || DRAG_TYPES.has(item.type),
  );
}
