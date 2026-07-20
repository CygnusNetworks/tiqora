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
  it("shows the localised state label for non-new tickets", () => {
    wrap(makeTicket({ state: "pending reminder", state_type: "pending reminder" }));
    expect(screen.getByTestId("ticket-meta-line")).toHaveTextContent("Pending reminder");
    expect(screen.queryByTestId("ticket-header-new-badge")).toBeNull();
  });

  it("shows only the Neu/New badge for new tickets (no duplicate state text)", () => {
    wrap(makeTicket({ state: "new", state_type: "new" }));
    expect(screen.getByTestId("ticket-header-new-badge")).toHaveTextContent("New");
    // Meta line must not repeat the raw English state name.
    const meta = screen.getByTestId("ticket-meta-line");
    expect(meta).not.toHaveTextContent(/^.*\bnew\b/i);
    expect(meta.textContent?.toLowerCase()).not.toContain("new");
  });

  it("anchors the overflow menu top-right", () => {
    wrap(makeTicket(), <button data-testid="overflow-stub">⋮</button>);
    expect(screen.getByTestId("ticket-header-overflow")).toContainElement(
      screen.getByTestId("overflow-stub"),
    );
  });
});
