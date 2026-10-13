import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { LinkedTickets } from "./LinkedTickets";

const { listTicketLinks, unlinkTicket, linkGraph, itsmStatus } = vi.hoisted(() => ({
  listTicketLinks: vi.fn(),
  unlinkTicket: vi.fn(),
  linkGraph: vi.fn(),
  itsmStatus: vi.fn(),
}));

vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, unlinkTicket, linkGraph, status: itsmStatus } };
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
    itsmStatus.mockReset().mockResolvedValue({ mode: "off" });
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

  it("offers the note dialog only while the ITSM is on", async () => {
    listTicketLinks.mockReset().mockResolvedValue([LINK]);
    itsmStatus.mockReset().mockResolvedValue({ mode: "off" });
    const first = wrap(<LinkedTickets ticketId={7} />);
    await screen.findByTestId("linked-ticket-9");
    await waitFor(() => expect(itsmStatus).toHaveBeenCalled());
    expect(screen.queryByTestId("linked-note-open")).toBeNull();
    first.unmount();
    itsmStatus.mockReset().mockResolvedValue({ mode: "znuny_readthrough" });
    wrap(<LinkedTickets ticketId={7} />);
    fireEvent.click(await screen.findByTestId("linked-note-open"));
    expect(await screen.findByTestId("linked-note-dialog")).toBeTruthy();
  });

  it("toggles the link graph", async () => {
    listTicketLinks.mockReset().mockResolvedValue([LINK]);
    itsmStatus.mockReset().mockResolvedValue({ mode: "off" });
    linkGraph.mockReset().mockResolvedValue({ root: "ticket:7", truncated: false, nodes: [], edges: [] });
    wrap(<LinkedTickets ticketId={7} />);
    fireEvent.click(await screen.findByTestId("link-graph-toggle"));
    expect(await screen.findByTestId("link-graph")).toBeTruthy();
    fireEvent.click(screen.getByTestId("link-graph-toggle"));
    expect(screen.queryByTestId("link-graph")).toBeNull();
  });
});
