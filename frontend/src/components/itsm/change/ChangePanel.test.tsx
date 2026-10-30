import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { makeItsm, makeTicket } from "@/components/itsm/testSupport";
import { changeError, makeChange, renderWithClient } from "./changeTestSupport";
import { ChangePanel } from "./ChangePanel";

const { ticketItsm, get, adopt, record, status } = vi.hoisted(() => ({ ticketItsm: vi.fn(), get: vi.fn(), adopt: vi.fn(), record: vi.fn(), status: vi.fn() }));
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, ticketItsm } };
});
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, get, adopt, status } };
});
// The record has its own tests; here it only shows what it was given.
vi.mock("./ChangeRecord", () => ({
  ChangeRecord: (props: { change: { ticket_id: number }; embedApprovals: boolean }) => {
    record(props);
    return <div data-testid="change-record-stub" />;
  },
}));

const CHANGE_SUMMARY = { change_type: "normal", phase: "assessment", risk_level: "medium", planned_start: null, planned_end: null };

function renderPanel(ticket = makeTicket({ id: 130 })) {
  return renderWithClient(<ChangePanel ticket={ticket} embedApprovals={false} />);
}

describe("ChangePanel", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    for (const f of [ticketItsm, get, adopt, record, status]) f.mockReset();
    status.mockResolvedValue({ can_see_legacy: true, legacy: "live" });
  });
  afterEach(cleanup);

  it("renders nothing while ITSM is off or for other tickets", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ enabled: false, role: "change" }));
    renderPanel();
    await waitFor(() => expect(ticketItsm).toHaveBeenCalled());
    expect(screen.queryByTestId("change-panel")).toBeNull();
    cleanup();
    ticketItsm.mockResolvedValue(makeItsm({ mode: "tiqora", role: "incident" }));
    renderPanel();
    await waitFor(() => expect(ticketItsm).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("change-panel")).toBeNull();
  });

  it("shows the change record of a change ticket without the approval UI", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ mode: "tiqora", role: "change", change: CHANGE_SUMMARY }));
    get.mockResolvedValue(makeChange());
    renderPanel();
    expect(await screen.findByTestId("change-record-stub")).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith(130, expect.anything());
    expect(record).toHaveBeenCalledWith(expect.objectContaining({ embedApprovals: false }));
    expect(screen.getByTestId("change-panel-open-page")).toHaveAttribute("href", "/agent/itsm/changes/130");
  });

  it("offers to adopt a change ticket that has no record yet", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ mode: "tiqora", role: "change", change: null }));
    adopt.mockResolvedValue(makeChange({ phase: "draft", change_type: "emergency" }));
    renderPanel();
    expect(await screen.findByTestId("change-adopt")).toHaveTextContent("noch nicht als Change geführt");
    fireEvent.click(screen.getByTestId("change-adopt-type"));
    fireEvent.click(await screen.findByTestId("change-adopt-type-menu-option-emergency"));
    fireEvent.click(screen.getByTestId("change-adopt-submit"));
    await waitFor(() => expect(adopt).toHaveBeenCalledWith(130, "emergency"));
    expect(get).not.toHaveBeenCalled();
  });

  it("names a refused adoption and offers nothing without write access", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ mode: "tiqora", role: "change", change: null }));
    adopt.mockRejectedValue(changeError(409, { code: "ticket_closed" }));
    renderPanel();
    fireEvent.click(await screen.findByTestId("change-adopt-submit"));
    expect(await screen.findByTestId("change-adopt-error")).toHaveTextContent("Das Ticket ist geschlossen");
    cleanup();
    renderPanel(makeTicket({ id: 130, can_write: false, permissions: { ro: true, rw: false, note: false, owner: false, priority: false, move_into: false, create: false } }));
    expect(await screen.findByTestId("change-adopt")).toBeInTheDocument();
    expect(screen.queryByTestId("change-adopt-submit")).toBeNull();
  });

  it("shows a hint card with the Znuny changes in Mode A", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ mode: "znuny_readthrough", role: "change" }));
    renderPanel();
    const card = await screen.findByTestId("change-mode-a-hint");
    expect(card).toHaveTextContent("Znuny");
    expect(await screen.findByTestId("change-mode-a-link")).toHaveAttribute("href", "/agent/itsm/legacy-changes?ticket_id=130");
    expect(get).not.toHaveBeenCalled();
  });

  it("keeps the hint but drops the link for an agent who may not see the Znuny changes", async () => {
    status.mockResolvedValue({ can_see_legacy: false, legacy: "live" });
    ticketItsm.mockResolvedValue(makeItsm({ mode: "znuny_readthrough", role: "change" }));
    renderPanel();
    expect(await screen.findByTestId("change-mode-a-hint")).toHaveTextContent("Modus A");
    await waitFor(() => expect(status).toHaveBeenCalled());
    expect(screen.queryByTestId("change-mode-a-link")).toBeNull();
  });

  it("shows the parent change, kind and window of a change task", async () => {
    ticketItsm.mockResolvedValue(
      makeItsm({
        mode: "tiqora",
        role: "change_task",
        change_task: { change_ticket_id: 130, change_tn: "20261012130", position: 2, task_kind: "backout", planned_start: "2026-10-24T20:00:00Z", planned_end: "2026-10-24T21:00:00Z" },
      }),
    );
    renderPanel(makeTicket({ id: 143 }));
    const card = await screen.findByTestId("change-task-card");
    expect(screen.getByTestId("change-task-parent")).toHaveAttribute("href", "/agent/itsm/changes/130");
    expect(card).toHaveTextContent("20261012130");
    expect(card).toHaveTextContent("Rückfall");
    expect(card).toHaveTextContent("Schritt 2");
    expect(card).toHaveTextContent("2026-10-24 20:00 – 2026-10-24 21:00");
  });

  it("does not link a parent change the agent cannot read", async () => {
    ticketItsm.mockResolvedValue(
      makeItsm({
        mode: "tiqora",
        role: "change_task",
        change_task: { change_ticket_id: 130, change_tn: null, position: 1, task_kind: "workorder", planned_start: null, planned_end: null },
      }),
    );
    renderPanel(makeTicket({ id: 143 }));
    expect(await screen.findByTestId("change-task-card")).toHaveTextContent("Change ohne Leserecht");
    expect(screen.queryByTestId("change-task-parent")).toBeNull();
  });
});
