import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { LinkedTickets } from "./LinkedTickets";

const { listTicketLinks } = vi.hoisted(() => ({ listTicketLinks: vi.fn() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { listTicketLinks } };
});

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    params,
    to: _to,
    ...rest
  }: {
    children: React.ReactNode;
    params?: { ticketId: string };
    to?: string;
    [k: string]: unknown;
  }) => (
    <a href={`/agent/tickets/${params?.ticketId}`} {...rest}>
      {children}
    </a>
  ),
}));

function wrap(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("LinkedTickets", () => {
  it("renders nothing without links", async () => {
    listTicketLinks.mockReset().mockResolvedValue([]);
    wrap(<LinkedTickets ticketId={7} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId("linked-tickets")).toBeNull();
  });

  it("shows relation, number, title and state, linking to the other ticket", async () => {
    listTicketLinks.mockReset().mockResolvedValue([
      {
        source_key: "7",
        target_key: "9",
        link_type: "ParentChild",
        state: "Valid",
        other_ticket_id: 9,
        other_tn: "2026092610000038",
        other_title: "Zweites Ticket",
        other_state: "open",
        other_state_type: "open",
        other_role: "child",
      },
    ]);
    wrap(<LinkedTickets ticketId={7} />);
    const chip = await screen.findByTestId("linked-ticket-9");
    expect(chip.getAttribute("href")).toBe("/agent/tickets/9");
    expect(chip.textContent).toContain("2026092610000038");
    expect(chip.textContent).toContain("Zweites Ticket");
  });
});
