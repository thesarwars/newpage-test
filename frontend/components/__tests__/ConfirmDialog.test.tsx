import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "@/components/ConfirmDialog";

function setup(overrides: Partial<Parameters<typeof ConfirmDialog>[0]> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ConfirmDialog
      open
      title="Delete everything?"
      body="This cannot be undone."
      confirmLabel="Delete everything"
      destructive
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...overrides}
    />,
  );
  return { onConfirm, onCancel };
}

describe("destructive confirmation", () => {
  it("focuses Cancel, never the destructive action", async () => {
    // Someone who hits Enter reflexively should not have deleted their
    // workspace. This is the single most important line in the component.
    setup();

    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("announces itself as an alertdialog", () => {
    // Not `dialog`: this interrupts to confirm something irreversible, and
    // screen readers announce the two differently.
    setup();

    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });

  it("is labelled and described by its own content", () => {
    setup();
    const dialog = screen.getByRole("alertdialog");

    expect(dialog).toHaveAccessibleName("Delete everything?");
    expect(dialog).toHaveAccessibleDescription("This cannot be undone.");
  });

  it("confirms only when the confirm button is pressed", async () => {
    const user = userEvent.setup();
    const { onConfirm, onCancel } = setup();

    await user.click(screen.getByRole("button", { name: "Delete everything" }));

    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("disables both actions while the work is in flight", () => {
    // Otherwise a double-click sends the delete twice, and the second one 401s
    // against the session the first one just destroyed.
    setup({ busy: true });

    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Working…" })).toBeDisabled();
  });

  it("stays closed when not open", () => {
    render(
      <ConfirmDialog
        open={false}
        title="Nope"
        body="Nope"
        confirmLabel="Nope"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByRole("alertdialog", { hidden: true })).not.toHaveAttribute("open");
  });
});
