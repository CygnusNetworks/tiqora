import { beforeEach, describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { LinkedTickets } from "./LinkedTickets";

const { listTicketLinks, unlinkTicket, linkGraph, ticketItsm } = vi.hoisted(() => ({
  listTicketLinks: vi.fn(),
  unlinkTicket: vi.fn(),
  linkGraph: vi.fn(),
  ticketItsm: vi.fn(),
}));

vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, unlinkTicket, linkGraph, ticketItsm } };
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { listTicketLinks } };
});

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
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
  beforeEach(() => {
    // ITSM off: no CI links, and the row's own features stay
    ticketItsm.mockReset().mockResolvedValue({ enabled: false, mode: "off", ci_count: null });
  });

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

  const LINK = {
    source_key: "7", target_key: "9", link_type: "ParentChild", state: "Valid",
    other_ticket_id: 9, other_tn: "2026092610000038", other_title: "Zweites Ticket",
    other_state: "open", other_state_type: "open", other_role: "child",
  };

  it("removes a link after confirming, via DELETE, and refreshes the list", async () => {
    listTicketLinks.mockReset().mockResolvedValue([LINK]);
    unlinkTicket.mockReset().mockResolvedValue(undefined);
    wrap(<LinkedTickets ticketId={7} ticketTn="2026092610000011" />);
    fireEvent.click(await screen.findByTestId("linked-ticket-remove-9"));
    const dlg = await screen.findByTestId("confirm-dialog");
    expect(dlg.textContent).toContain("2026092610000011");
    expect(dlg.textContent).toContain("2026092610000038");
    expect(unlinkTicket).not.toHaveBeenCalled();
    listTicketLinks.mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: /entfernen|remove/i, description: "" }) as HTMLElement);
    await waitFor(() => expect(unlinkTicket).toHaveBeenCalledWith(7, 9, "ParentChild"));
    await waitFor(() => expect(screen.queryByTestId("linked-tickets")).toBeNull());
  });

  it("offers the note dialog in every ITSM mode, also when ITSM is off", async () => {
    listTicketLinks.mockReset().mockResolvedValue([LINK]);
    wrap(<LinkedTickets ticketId={7} />);
    fireEvent.click(await screen.findByTestId("linked-note-open"));
    expect(await screen.findByTestId("linked-note-dialog")).toBeTruthy();
  });

  it("hides remove and note without rw on the ticket", async () => {
    listTicketLinks.mockReset().mockResolvedValue([LINK]);
    wrap(<LinkedTickets ticketId={7} canWrite={false} />);
    await screen.findByTestId("linked-ticket-9");
    expect(screen.queryByTestId("linked-ticket-remove-9")).toBeNull();
    expect(screen.queryByTestId("linked-note-open")).toBeNull();
    expect(screen.getByTestId("link-graph-toggle")).toBeTruthy();
  });

  it("offers the graph for a ticket with only CI links", async () => {
    listTicketLinks.mockReset().mockResolvedValue([]);
    ticketItsm.mockResolvedValue({ enabled: true, mode: "znuny_readthrough", ci_count: 2 });
    wrap(<LinkedTickets ticketId={7} />);
    expect(await screen.findByTestId("link-graph-toggle")).toBeTruthy();
    expect(screen.queryByTestId("linked-note-open")).toBeNull();
  });

  it("toggles the link graph", async () => {
    listTicketLinks.mockReset().mockResolvedValue([LINK]);
    linkGraph.mockReset().mockResolvedValue({ root: "ticket:7", truncated: false, nodes: [], edges: [] });
    wrap(<LinkedTickets ticketId={7} />);
    fireEvent.click(await screen.findByTestId("link-graph-toggle"));
    expect(await screen.findByTestId("link-graph")).toBeTruthy();
    fireEvent.click(screen.getByTestId("link-graph-toggle"));
    expect(screen.queryByTestId("link-graph")).toBeNull();
  });
});
