import { describe, it, expect } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";
import { useState } from "react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { Dialog } from "./Dialog";

/** Dialog whose parent re-renders with a fresh `onClose` identity on every
 * render — exactly what happens when a composer's debounced draft autosave
 * updates a query the parent subscribes to. */
function Harness() {
  const [, setTick] = useState(0);
  const [open, setOpen] = useState(false);
  return (
    <I18nextProvider i18n={i18n}>
      <button data-testid="opener" onClick={() => setOpen(true)}>
        open
      </button>
      <button data-testid="rerender" onClick={() => setTick((n) => n + 1)}>
        rerender
      </button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Compose">
        <input data-testid="first" />
        <textarea data-testid="second" />
      </Dialog>
    </I18nextProvider>
  );
}

/** Open the dialog the way a user does: focus the opener, then click it —
 * the Dialog records `document.activeElement` as the element to restore. */
async function openDialog() {
  const opener = screen.getByTestId("opener");
  opener.focus();
  act(() => {
    opener.click();
  });
  await waitFor(() => {
    expect(screen.getByTestId("first")).toHaveFocus();
  });
}

describe("Dialog focus management", () => {
  it("moves focus to the first form control on open", async () => {
    render(<Harness />);
    await openDialog();
  });

  it("keeps focus where the user put it when the parent re-renders", async () => {
    render(<Harness />);
    await openDialog();

    // The user moved on to a later field and is typing there.
    screen.getByTestId("second").focus();
    expect(screen.getByTestId("second")).toHaveFocus();

    // Parent re-renders (draft autosave, query update, …) — `onClose` gets a
    // new identity. Focus must NOT jump back to the first field.
    act(() => {
      screen.getByTestId("rerender").click();
    });
    // Let any (wrongly) scheduled focus-move rAF run before asserting.
    await act(() => new Promise((r) => requestAnimationFrame(() => r(undefined))));

    expect(screen.getByTestId("second")).toHaveFocus();
  });
});
