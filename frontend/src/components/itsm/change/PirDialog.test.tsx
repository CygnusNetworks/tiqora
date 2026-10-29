import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { changeError, makeChange, renderWithClient } from "./changeTestSupport";
import { PirDialog } from "./PirDialog";

const { transition } = vi.hoisted(() => ({ transition: vi.fn() }));
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, transition } };
});

async function pickOutcome(value: string) {
  fireEvent.click(screen.getByTestId("change-pir-outcome"));
  fireEvent.click(await screen.findByTestId(`change-pir-outcome-menu-option-${value}`));
}

describe("PirDialog", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    transition.mockReset();
  });
  afterEach(cleanup);

  it("closes a change with its outcome and the review notes", async () => {
    transition.mockResolvedValue(makeChange({ phase: "closed" }));
    const onDone = vi.fn();
    renderWithClient(<PirDialog change={makeChange({ phase: "review" })} to="closed" onClose={vi.fn()} onDone={onDone} />);
    expect(screen.getByRole("dialog")).toHaveTextContent("Change abschließen");
    // the outcome is required to close
    expect(screen.getByTestId("change-transition-submit")).toBeDisabled();
    await pickOutcome("backed_out");
    fireEvent.change(screen.getByTestId("change-pir-notes"), { target: { value: "Rückfall nach 20 Minuten" } });
    fireEvent.click(screen.getByTestId("change-transition-submit"));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(transition).toHaveBeenCalledWith(130, { to: "closed", outcome: "backed_out", pir_notes: "Rückfall nach 20 Minuten" });
  });

  it("asks for the review notes before a normal change is closed from the review", async () => {
    renderWithClient(<PirDialog change={makeChange({ phase: "review" })} to="closed" onClose={vi.fn()} onDone={vi.fn()} />);
    await pickOutcome("successful");
    expect(screen.getByTestId("change-pir-notes-required")).toBeInTheDocument();
    expect(screen.getByTestId("change-transition-submit")).toBeDisabled();
  });

  it("starts the review without an outcome and keeps the notes already written", async () => {
    transition.mockResolvedValue(makeChange({ phase: "review" }));
    renderWithClient(
      <PirDialog change={makeChange({ phase: "implementing", pir_notes: "Erste Notiz" })} to="review" onClose={vi.fn()} onDone={vi.fn()} />,
    );
    expect(screen.queryByTestId("change-pir-outcome")).toBeNull();
    expect(screen.getByTestId("change-pir-notes")).toHaveValue("Erste Notiz");
    fireEvent.click(screen.getByTestId("change-transition-submit"));
    await waitFor(() => expect(transition).toHaveBeenCalledWith(130, { to: "review", pir_notes: "Erste Notiz" }));
  });

  it("shows a refused close like any transition", async () => {
    transition.mockRejectedValue(changeError(409, { code: "transition_refused", failures: [{ code: "pir_missing", overridable: false }] }));
    renderWithClient(
      <PirDialog change={makeChange({ phase: "implementing", change_type: "standard" })} to="closed" onClose={vi.fn()} onDone={vi.fn()} />,
    );
    await pickOutcome("successful");
    fireEvent.click(screen.getByTestId("change-transition-submit"));
    expect(await screen.findByTestId("change-guard-failures")).toHaveTextContent("Die Nachbetrachtung fehlt.");
  });
});
