import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { TicketDetail } from "@/lib/api";
import { TicketHeader } from "./TicketHeader";

function makeTicket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: 7,
    tn: "20240601000001",
    title: "Printer jam",
    queue_id: 1,
    queue_name: "Support",
    state_id: 4,
    state: "open",
    state_type: "open",
    priority_id: 3,
    priority: "3 normal",
    lock_id: 1,
    lock: "unlock",
    owner_id: 2,
    owner_name: "Ada",
    create_time: "2024-06-01T12:00:00Z",
    change_time: "2024-06-01T12:00:00Z",
    is_watched: false,
    can_write: true,
    ...overrides,
  } as TicketDetail;
}

function wrap(ticket: TicketDetail, overflowMenu?: React.ReactNode) {
  return render(
    <I18nextProvider i18n={i18n}>
      <TicketHeader ticket={ticket} overflowMenu={overflowMenu} />
    </I18nextProvider>,
  );
}

describe("TicketHeader", () => {
  it("shows status and priority as soft-chips in the meta line", () => {
    wrap(makeTicket({ state: "pending reminder", state_type: "pending reminder" }));
    const stateChip = screen.getByTestId("ticket-header-state-chip");
    expect(stateChip).toHaveTextContent("Pending reminder");
    expect(stateChip).toHaveAttribute("data-kind", "state");
    expect(stateChip).toHaveStyle({ color: "var(--color-state-pending)" });

    const prioChip = screen.getByTestId("ticket-header-priority-chip");
    expect(prioChip).toHaveTextContent("normal");
    expect(prioChip).toHaveAttribute("data-kind", "priority");
    expect(prioChip).toHaveStyle({ color: "var(--color-prio-3)" });

    // Old NEU-only badge special-case is gone.
    expect(screen.queryByTestId("ticket-header-new-badge")).toBeNull();
  });

  it("shows the same soft-chip for new tickets (no separate Neu badge)", () => {
    wrap(makeTicket({ state: "new", state_type: "new" }));
    const stateChip = screen.getByTestId("ticket-header-state-chip");
    expect(stateChip).toHaveTextContent("New");
    expect(stateChip).toHaveStyle({ color: "var(--color-state-new)" });
    expect(screen.queryByTestId("ticket-header-new-badge")).toBeNull();
  });

  it("anchors the overflow menu top-right", () => {
    wrap(makeTicket(), <button data-testid="overflow-stub">⋮</button>);
    expect(screen.getByTestId("ticket-header-overflow")).toContainElement(
      screen.getByTestId("overflow-stub"),
    );
  });
});
