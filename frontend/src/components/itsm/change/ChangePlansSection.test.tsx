import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { changeError, makeChange, renderWithClient } from "./changeTestSupport";
import { ChangePlansSection } from "./ChangePlansSection";

const { update } = vi.hoisted(() => ({ update: vi.fn() }));
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, update } };
});

describe("ChangePlansSection", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    update.mockReset();
  });
  afterEach(cleanup);

  it("shows the window, the reason and the three plans", () => {
    renderWithClient(<ChangePlansSection change={makeChange()} />);
    const section = screen.getByTestId("change-plans");
    expect(section).toHaveTextContent("2026-10-24 20:00 – 2026-10-25 00:00");
    expect(section).toHaveTextContent("Bekannte Sicherheitslücke");
    expect(screen.getByTestId("change-plan-implementation_plan")).toHaveTextContent("Firmware einspielen");
    expect(screen.getByTestId("change-plan-backout_plan")).toHaveTextContent("Alte Firmware zurückspielen");
    expect(screen.getByTestId("change-plan-test_plan")).toHaveTextContent("Ping auf alle Ports");
  });

  it("saves only the fields that were changed", async () => {
    update.mockResolvedValue(makeChange({ backout_plan: "Snapshot zurückspielen" }));
    renderWithClient(<ChangePlansSection change={makeChange()} />);
    fireEvent.click(screen.getByTestId("change-plans-edit"));
    fireEvent.change(screen.getByTestId("change-plans-input-backout_plan"), { target: { value: "Snapshot zurückspielen" } });
    fireEvent.change(screen.getByTestId("change-plans-end"), { target: { value: "2026-10-25T02:00" } });
    fireEvent.click(screen.getByTestId("change-plans-save"));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(130, {
        backout_plan: "Snapshot zurückspielen",
        planned_start: "2026-10-24T20:00:00Z",
        planned_end: "2026-10-25T02:00:00Z",
      }),
    );
    await waitFor(() => expect(screen.queryByTestId("change-plans-save")).toBeNull());
  });

  it("clears the window when both times are emptied and refuses a half window", async () => {
    update.mockResolvedValue(makeChange({ planned_start: null, planned_end: null }));
    renderWithClient(<ChangePlansSection change={makeChange()} />);
    fireEvent.click(screen.getByTestId("change-plans-edit"));
    fireEvent.change(screen.getByTestId("change-plans-start"), { target: { value: "" } });
    expect(screen.getByTestId("change-plans-window-invalid")).toBeInTheDocument();
    expect(screen.getByTestId("change-plans-save")).toBeDisabled();
    fireEvent.change(screen.getByTestId("change-plans-end"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("change-plans-save"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(130, { clear_window: true }));
  });

  it("shows the API's refusal", async () => {
    update.mockRejectedValue(changeError(409, { code: "change_locked_in_approval", field: "backout_plan" }));
    renderWithClient(<ChangePlansSection change={makeChange()} />);
    fireEvent.click(screen.getByTestId("change-plans-edit"));
    fireEvent.change(screen.getByTestId("change-plans-input-reason"), { target: { value: "Neu" } });
    fireEvent.click(screen.getByTestId("change-plans-save"));
    expect(await screen.findByTestId("change-plans-error")).toHaveTextContent("Während der Freigabe");
  });

  it("locks window and plans while the CAB decides, but not the reason", async () => {
    update.mockResolvedValue(makeChange({ phase: "approval", reason: "Neue Begründung" }));
    renderWithClient(<ChangePlansSection change={makeChange({ phase: "approval" })} />);
    expect(screen.getByTestId("change-plans-locked")).toHaveTextContent("Während der Freigabe");
    fireEvent.click(screen.getByTestId("change-plans-edit"));
    expect(screen.queryByTestId("change-plans-start")).toBeNull();
    expect(screen.queryByTestId("change-plans-input-backout_plan")).toBeNull();
    fireEvent.change(screen.getByTestId("change-plans-input-reason"), { target: { value: "Neue Begründung" } });
    fireEvent.click(screen.getByTestId("change-plans-save"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(130, { reason: "Neue Begründung" }));
  });

  it("is read-only on a final change", () => {
    renderWithClient(<ChangePlansSection change={makeChange({ phase: "closed", pir_notes: "Lief gut" })} />);
    expect(screen.queryByTestId("change-plans-edit")).toBeNull();
    expect(screen.getByTestId("change-plan-pir_notes")).toHaveTextContent("Lief gut");
  });

  it("offers no editing without write access", () => {
    renderWithClient(<ChangePlansSection change={makeChange({ can_write: false })} />);
    expect(screen.queryByTestId("change-plans-edit")).toBeNull();
  });
});
