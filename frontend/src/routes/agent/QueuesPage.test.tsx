import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
} from "@tanstack/react-router";
import i18n from "@/i18n";
import type { TicketListItem } from "@/lib/api";
import { QueuesPage } from "./QueuesPage";

const {
  listQueues,
  listTickets,
  patchTicket,
  listReferenceStates,
  listReferencePriorities,
  listReferenceAgents,
  exportTicketsCsvUrl,
} = vi.hoisted(() => ({
  listQueues: vi.fn(),
  listTickets: vi.fn(),
  patchTicket: vi.fn(),
  listReferenceStates: vi.fn(),
  listReferencePriorities: vi.fn(),
  listReferenceAgents: vi.fn(),
  exportTicketsCsvUrl: vi.fn(() => "/export.csv"),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      listQueues,
      listTickets,
      patchTicket,
      listReferenceStates,
      listReferencePriorities,
      listReferenceAgents,
      exportTicketsCsvUrl,
    },
  };
});

function makeTicket(overrides: Partial<TicketListItem> & { id: number }): TicketListItem {
  return {
    tn: `2024060100${overrides.id}`,
    title: `Ticket ${overrides.id}`,
    queue_id: 1,
    queue_name: "Support",
    state_id: 4,
    state: "open",
    state_type: "open",
    priority_id: 3,
    priority: "3 normal",
    lock_id: 1,
    lock: "unlock",
    owner_id: 5,
    create_time: "2024-06-01T12:00:00",
    change_time: "2024-06-01T12:00:00",
    escalation_time: 0,
    escalation_response_time: 0,
    escalation_update_time: 0,
    escalation_solution_time: 0,
    until_time: 0,
    attachment_count: 0,
    has_ai_summary: false,
    ...overrides,
  };
}

function page(items: TicketListItem[], total = items.length) {
  return { items, limit: 50, offset: 0, total };
}

async function renderQueuesPage(initialSearch: Record<string, unknown> = {}) {
  const rootRoute = createRootRoute();
  const queuesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/agent/queues",
    component: () => <QueuesPage />,
    validateSearch: (s: Record<string, unknown>) => s,
  });
  const ticketRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/agent/tickets/$ticketId",
  });
  const searchStr = new URLSearchParams(
    initialSearch as Record<string, string>,
  ).toString();
  const router = createRouter({
    routeTree: rootRoute.addChildren([queuesRoute, ticketRoute]),
    history: createMemoryHistory({
      initialEntries: [`/agent/queues${searchStr ? `?${searchStr}` : ""}`],
    }),
  });
  await router.load();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

const tickets = [
  makeTicket({ id: 101 }),
  makeTicket({ id: 102 }),
  makeTicket({ id: 103 }),
];

describe("QueuesPage selection mode (Auswahlmodus)", () => {
  beforeEach(() => {
    listQueues.mockReset();
    listTickets.mockReset();
    patchTicket.mockReset();
    listReferenceStates.mockReset();
    listReferencePriorities.mockReset();
    listReferenceAgents.mockReset();
    void i18n.changeLanguage("de");

    listQueues.mockResolvedValue([]);
    listTickets.mockResolvedValue(page(tickets, tickets.length));
    listReferenceStates.mockResolvedValue([
      { id: 1, name: "new", type_name: "new" },
      { id: 4, name: "open", type_name: "open" },
    ]);
    listReferencePriorities.mockResolvedValue([
      { id: 3, name: "3 normal" },
      { id: 4, name: "4 high" },
    ]);
    listReferenceAgents.mockResolvedValue([
      { id: 5, login: "agent1", full_name: "Ada Agent" },
      { id: 6, login: "agent2", full_name: "Bob Agent" },
    ]);
    patchTicket.mockResolvedValue(undefined);
  });

  it("enters selection mode, shows checkboxes, and row click toggles instead of navigating", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    expect(screen.queryByTestId("queue-row-check-101")).toBeNull();

    fireEvent.click(screen.getByTestId("queue-select-mode"));

    await waitFor(() => {
      expect(screen.getByTestId("queue-row-check-101")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("ticket-row-101"));
    expect(screen.getByTestId("queue-row-check-101")).toBeChecked();
    expect(screen.getByTestId("queue-selected-count").textContent).toMatch(/1/);

    // Clicking again toggles off.
    fireEvent.click(screen.getByTestId("ticket-row-101"));
    expect(screen.getByTestId("queue-row-check-101")).not.toBeChecked();
  });

  it("Escape exits selection mode and clears the selection", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");
    fireEvent.click(screen.getByTestId("queue-select-mode"));
    await waitFor(() => expect(screen.getByTestId("queue-row-check-101")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("ticket-row-101"));
    expect(screen.getByTestId("queue-row-check-101")).toBeChecked();

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByTestId("queue-select-banner")).toBeNull();
    });
    expect(screen.getByTestId("queue-select-mode")).toBeInTheDocument();
  });

  it("header checkbox selects all loaded rows on the page", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");
    fireEvent.click(screen.getByTestId("queue-select-mode"));
    await waitFor(() => expect(screen.getByTestId("queue-select-all-page")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("queue-select-all-page"));

    expect(screen.getByTestId("queue-row-check-101")).toBeChecked();
    expect(screen.getByTestId("queue-row-check-102")).toBeChecked();
    expect(screen.getByTestId("queue-row-check-103")).toBeChecked();
    expect(screen.getByTestId("queue-selected-count").textContent).toMatch(/3/);
  });

  it("select-all-matches fetches remaining ids across pages and updates the banner", async () => {
    const bigTotal = 250;
    const firstPage = Array.from({ length: 3 }, (_, i) => makeTicket({ id: 100 + i }));
    listTickets.mockImplementation(
      async (params: { offset?: number; limit?: number } = {}) => {
        const offset = params.offset ?? 0;
        const limit = params.limit ?? 50;
        // The page's own list query always asks for the default page limit (50);
        // the "select all matches" id-fetch walks SELECT_ALL_PAGE_SIZE (200) pages.
        if (limit === 50) return page(firstPage, bigTotal);
        const start = offset;
        const end = Math.min(start + limit, bigTotal);
        const items = [];
        for (let i = start; i < end; i++) items.push(makeTicket({ id: 1000 + i }));
        return page(items, bigTotal);
      },
    );

    await renderQueuesPage();
    await screen.findByTestId("ticket-row-100");
    fireEvent.click(screen.getByTestId("queue-select-mode"));
    await waitFor(() => expect(screen.getByTestId("queue-select-all-matches")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("queue-select-all-matches"));

    await waitFor(() => {
      expect(screen.getByTestId("queue-select-all-status").textContent).toMatch(/250/);
    });
    expect(screen.getByTestId("queue-select-all-status").textContent).not.toMatch(/begrenzt/);
  });

  it("state change: dropdown -> confirm dialog -> patchTicket per id -> success clears selection", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");
    fireEvent.click(screen.getByTestId("queue-select-mode"));
    await waitFor(() => expect(screen.getByTestId("queue-row-check-101")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("ticket-row-101"));
    fireEvent.click(screen.getByTestId("ticket-row-102"));

    fireEvent.click(screen.getByTestId("queue-bulk-state"));
    const option = await screen.findByTestId("queue-bulk-state-menu-option-4");
    fireEvent.click(option);

    const confirmButton = await screen.findByTestId("queue-bulk-confirm");
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(screen.getByTestId("queue-bulk-status")).toBeInTheDocument();
    });
    expect(patchTicket).toHaveBeenCalledTimes(2);
    expect(patchTicket).toHaveBeenCalledWith(101, { state_id: 4 });
    expect(patchTicket).toHaveBeenCalledWith(102, { state_id: 4 });
    expect(screen.getByTestId("queue-bulk-status").textContent).toMatch(/2/);
    // Selection cleared for the succeeded ids.
    expect(screen.queryByTestId("queue-selected-count")?.textContent ?? "0").toMatch(/0/);
  });

  it("partial failure keeps failed ids selected and reports a partial-fail status", async () => {
    patchTicket.mockImplementation(async (id: number) => {
      if (id === 102) throw new Error("boom");
    });

    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");
    fireEvent.click(screen.getByTestId("queue-select-mode"));
    await waitFor(() => expect(screen.getByTestId("queue-row-check-101")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("ticket-row-101"));
    fireEvent.click(screen.getByTestId("ticket-row-102"));

    fireEvent.click(screen.getByTestId("queue-bulk-priority"));
    const option = await screen.findByTestId("queue-bulk-priority-menu-option-4");
    fireEvent.click(option);

    fireEvent.click(await screen.findByTestId("queue-bulk-confirm"));

    await waitFor(() => {
      expect(screen.getByTestId("queue-bulk-status")).toBeInTheDocument();
    });
    const statusText = screen.getByTestId("queue-bulk-status").textContent ?? "";
    expect(statusText).toMatch(/1/);
    expect(statusText).toMatch(/102/);
    // Failed ticket stays selected.
    expect(screen.getByTestId("queue-row-check-102")).toBeChecked();
    expect(screen.getByTestId("queue-row-check-101")).not.toBeChecked();
  });

  it("action dropdowns are disabled with an empty selection", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");
    fireEvent.click(screen.getByTestId("queue-select-mode"));

    await waitFor(() => expect(screen.getByTestId("queue-bulk-state")).toBeInTheDocument());
    expect(screen.getByTestId("queue-bulk-state")).toBeDisabled();
    expect(screen.getByTestId("queue-bulk-priority")).toBeDisabled();
    expect(screen.getByTestId("queue-bulk-owner")).toBeDisabled();
  });
});

describe("QueuesPage row quick edit", () => {
  beforeEach(() => {
    listQueues.mockReset();
    listTickets.mockReset();
    patchTicket.mockReset();
    listReferenceStates.mockReset();
    listReferencePriorities.mockReset();
    listReferenceAgents.mockReset();
    void i18n.changeLanguage("de");

    listQueues.mockResolvedValue([]);
    listTickets.mockResolvedValue(page(tickets, tickets.length));
    listReferenceStates.mockResolvedValue([
      { id: 1, name: "new", type_name: "new" },
      { id: 4, name: "open", type_name: "open" },
    ]);
    listReferencePriorities.mockResolvedValue([
      { id: 3, name: "3 normal" },
      { id: 4, name: "4 high" },
    ]);
    listReferenceAgents.mockResolvedValue([
      { id: 5, login: "agent1", full_name: "Ada Agent" },
      { id: 6, login: "agent2", full_name: "Bob Agent" },
    ]);
    patchTicket.mockResolvedValue(undefined);
  });

  it("does not fetch reference lists before any quick-edit menu is opened", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    expect(listReferenceStates).not.toHaveBeenCalled();
    expect(listReferencePriorities).not.toHaveBeenCalled();
    expect(listReferenceAgents).not.toHaveBeenCalled();
  });

  it("clicking a row's state cell lazily fetches states and patches only that ticket, without navigating", async () => {
    const router = await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    fireEvent.click(screen.getByTestId("ticket-row-state-101"));
    await waitFor(() => expect(listReferenceStates).toHaveBeenCalledTimes(1));

    const option = await screen.findByTestId("ticket-row-state-menu-101-option-4");
    fireEvent.click(option);

    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(101, { state_id: 4 }));
    expect(patchTicket).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe("/agent/queues");
  });

  it("clicking a row's owner cell patches owner_id", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    fireEvent.click(screen.getByTestId("ticket-row-owner-102"));
    const option = await screen.findByTestId("ticket-row-owner-menu-102-option-6");
    fireEvent.click(option);

    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(102, { owner_id: 6 }));
  });

  it("row quick edit is unavailable once selection mode is entered", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    fireEvent.click(screen.getByTestId("queue-select-mode"));
    await waitFor(() => expect(screen.getByTestId("queue-row-check-101")).toBeInTheDocument());

    expect(screen.queryByTestId("ticket-row-state-101")).toBeNull();
    expect(screen.queryByTestId("ticket-row-owner-101")).toBeNull();
  });
});
