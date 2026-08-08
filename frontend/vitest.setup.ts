import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// RTL registers its own afterEach(cleanup) only when vitest runs with
// `globals: true`. Without this, every render accumulates in the same document
// and the second test in a file finds two of everything — which surfaces as
// "Found multiple elements with the role", not as a missing-cleanup message.
afterEach(cleanup);

// jsdom implements <dialog> but not showModal/close, which ConfirmDialog and
// PasteSheet depend on for the focus trap and Escape handling this app gets
// from the platform rather than from a library. Without these, every dialog
// test throws rather than failing usefully.
if (typeof HTMLDialogElement !== "undefined") {
  HTMLDialogElement.prototype.showModal ??= function showModal(this: HTMLDialogElement) {
    this.open = true;
  };
  HTMLDialogElement.prototype.close ??= function close(this: HTMLDialogElement) {
    this.open = false;
    this.dispatchEvent(new Event("close"));
  };
}
