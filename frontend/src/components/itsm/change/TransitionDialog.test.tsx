import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { changeError, makeChange, renderWithClient } from "./changeTestSupport";
import { TransitionDialog } from "./TransitionDialog";

const { transition } = vi.hoisted(() => ({ transition: vi.fn() }));
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, transition } };
});

const refused = (failures: { code: string; overridable: boolean }[]) =>
  changeError(409, { code: "transition_refused", failures });

function renderDialog(change = makeChange(), to: Parameters<typeof TransitionDialog>[0]["to"] = "approval") {
  const onDone = vi.fn();
  const onClose = vi.fn();
  renderWithClient(<TransitionDialog change={change} to={to} onClose={onClose} onDone={onDone} />);
  return { onDone, onClose };
}

describe("TransitionDialog", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    transition.mockReset();
  });
  afterEach(cleanup);

  it("moves the change and hands the answer back", async () => {
    const moved = makeChange({ phase: "approval" });
    transition.mockResolvedValue(moved);
    const { onDone } = renderDialog();
    expect(screen.getByRole("dialog")).toHaveTextContent("Freigabe anfordern");
    fireEvent.change(screen.getByTestId("change-transition-reason"), { target: { value: "CAB am Dienstag" } });
    fireEvent.click(screen.getByTestId("change-transition-submit"));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(moved));
    expect(transition).toHaveBeenCalledWith(130, { to: "approval", reason: "CAB am Dienstag" });
  });

  it("lists the guard failures of a 409 and offers no override to a non-manager", async () => {
    transition.mockRejectedValue(
      refused([
        { code: "block_conflict", overridable: true },
        { code: "plans_missing", overridable: false },
      ]),
    );
    renderDialog(makeChange({ can_manage: false }));
    fireEvent.click(screen.getByTestId("change-transition-submit"));
    const list = await screen.findByTestId("change-guard-failures");
    expect(list).toHaveTextContent("Ein Sperrzeitraum blockiert das Zeitfenster.");
    expect(list).toHaveTextContent("Umsetzungs- und Rückfallplan fehlen.");
    expect(screen.queryByTestId("change-transition-override-reason")).toBeNull();
    expect(screen.queryByTestId("change-transition-force")).toBeNull();
  });

  it("lets a manager pass a block conflict with an override reason", async () => {
    transition.mockRejectedValueOnce(refused([{ code: "block_conflict", overridable: true }]));
    transition.mockResolvedValueOnce(makeChange({ phase: "approval" }));
    const { onDone } = renderDialog(makeChange({ can_manage: true }));
    fireEvent.click(screen.getByTestId("change-transition-submit"));
    const override = await screen.findByTestId("change-transition-override-reason");
    expect(screen.queryByTestId("change-transition-force")).toBeNull();
    fireEvent.change(override, { target: { value: "Sicherheitsupdate kann nicht warten" } });
    fireEvent.click(screen.getByTestId("change-transition-submit"));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(transition).toHaveBeenLastCalledWith(130, { to: "approval", override_reason: "Sicherheitsupdate kann nicht warten" });
  });

  it("lets a manager force past open tasks, but only with a reason", async () => {
    transition.mockRejectedValueOnce(refused([{ code: "tasks_open", overridable: true }]));
    transition.mockResolvedValueOnce(makeChange({ phase: "review" }));
    const { onDone } = renderDialog(makeChange({ can_manage: true, phase: "implementing" }), "review");
    fireEvent.click(screen.getByTestId("change-transition-submit"));
    fireEvent.click(await screen.findByTestId("change-transition-force"));
    expect(screen.getByTestId("change-transition-submit")).toBeDisabled();
    expect(screen.getByTestId("change-transition-reason-required")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("change-transition-reason"), { target: { value: "Aufgabe entfällt" } });
    fireEvent.click(screen.getByTestId("change-transition-submit"));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(transition).toHaveBeenLastCalledWith(130, { to: "review", reason: "Aufgabe entfällt", force: true });
  });

  it("names the CAB problems of an approval request", async () => {
    transition.mockRejectedValue(
      refused([
        { code: "cab_missing", overridable: false },
        { code: "cab_empty", overridable: false },
      ]),
    );
    renderDialog();
    fireEvent.click(screen.getByTestId("change-transition-submit"));
    const list = await screen.findByTestId("change-guard-failures");
    expect(list).toHaveTextContent("Es ist kein CAB gewählt");
    expect(list).toHaveTextContent("Das CAB hat keine Mitglieder.");
  });

  it("shows the engine's code when the approval request failed", async () => {
    transition.mockRejectedValue(changeError(409, { code: "approval_request_failed", engine_code: "no_approvers", message: "x" }));
    renderDialog();
    fireEvent.click(screen.getByTestId("change-transition-submit"));
    const error = await screen.findByTestId("change-transition-error");
    expect(error).toHaveTextContent("Die Freigabe konnte nicht angefordert werden.");
    expect(error).toHaveTextContent("no_approvers");
  });

  it("explains a change that is locked while the CAB decides", async () => {
    transition.mockRejectedValue(changeError(409, { code: "change_locked_in_approval" }));
    renderDialog();
    fireEvent.click(screen.getByTestId("change-transition-submit"));
    expect(await screen.findByTestId("change-transition-error")).toHaveTextContent("Während der Freigabe");
  });
});
