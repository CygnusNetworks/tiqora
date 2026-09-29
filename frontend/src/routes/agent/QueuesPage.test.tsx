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

const authUser = vi.hoisted(() => ({
  current: { id: 5, login: "agent1", is_admin: false } as { id: number; login: string; is_admin: boolean },
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: authUser.current }),
}));

const {
  listQueues,
  listTickets,
  patchTicket,
  listReferenceStates,
  listReferencePriorities,
  listReferenceAgents,
  exportTicketsCsvUrl,
  ticketFacets,
} = vi.hoisted(() => ({
  listQueues: vi.fn(),
  listTickets: vi.fn(),
  patchTicket: vi.fn(),
  listReferenceStates: vi.fn(),
  listReferencePriorities: vi.fn(),
  listReferenceAgents: vi.fn(),
  exportTicketsCsvUrl: vi.fn(() => "/export.csv"),
  ticketFacets: vi.fn(),
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
      ticketFacets,
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
    channel: "email",
    has_ai_summary: false,
    ai_escalated: false,
    archive_flag: 0,
    ...overrides,
  };
}

function page(items: TicketListItem[], total = items.length) {
  return { items, limit: 50, offset: 0, total };
}

type ListParams = { offset?: number; limit?: number; escalating_within?: number };

/** listTickets mock that serves `items` for the list and `pinned` for the
 * pinned "needs attention" query (the one asking for `escalating_within`). */
function serveTickets(items: TicketListItem[], pinned: TicketListItem[] = []) {
  listTickets.mockImplementation(async (params: ListParams = {}) =>
    params.escalating_within != null ? page(pinned) : page(items),
  );
}

const FACETS = {
  states: { todo: 3, new: 1, open_only: 2, pending: 4, closed: 9, all: 16 },
  flags: { escalated: 1, locked: 0, unassigned: 2 },
};

async function renderQueuesPage(initialSearch: Record<string, unknown> = {}) {
  const rootRoute = createRootRoute();
  const queuesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/agent/queues",
    component: () => <QueuesPage />,
    // Mirrors router.tsx's real validateSearch just enough for these tests:
    // the router's default codec parses all-digit query values (customer
    // numbers) as JS numbers, so customer_id must be normalized back to a
    // string the same way the production route does.
    validateSearch: (s: Record<string, unknown>) => ({
      ...s,
      customer_id:
        typeof s.customer_id === "string" && s.customer_id !== ""
          ? s.customer_id
          : typeof s.customer_id === "number"
            ? String(s.customer_id)
            : undefined,
    }),
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

function resetMocks() {
  listQueues.mockReset();
  listTickets.mockReset();
  patchTicket.mockReset();
  listReferenceStates.mockReset();
  listReferencePriorities.mockReset();
  listReferenceAgents.mockReset();
  ticketFacets.mockReset();
  exportTicketsCsvUrl.mockClear();
  void i18n.changeLanguage("de");

  listQueues.mockResolvedValue([]);
  serveTickets(tickets);
  ticketFacets.mockResolvedValue(FACETS);
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
  authUser.current = { id: 5, login: "agent1", is_admin: false };
}

describe("QueuesPage selection", () => {
  beforeEach(resetMocks);

  it("row checkboxes select without a mode switch; the action bar appears with the first pick", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    expect(screen.queryByTestId("queue-select-banner")).toBeNull();
    expect(screen.getByTestId("queue-row-check-101")).not.toBeChecked();

    fireEvent.click(screen.getByTestId("queue-row-check-101"));
    expect(screen.getByTestId("queue-row-check-101")).toBeChecked();
    expect(screen.getByTestId("queue-selected-count").textContent).toMatch(/1/);

    fireEvent.click(screen.getByTestId("queue-row-check-101"));
    expect(screen.getByTestId("queue-row-check-101")).not.toBeChecked();
    expect(screen.queryByTestId("queue-select-banner")).toBeNull();
  });

  it("remembers the list when a ticket is opened, for the ticket's back link and ‹ ›", async () => {
    window.sessionStorage.clear();
    await renderQueuesPage({ state_type: "pending" });
    await screen.findByTestId("ticket-row-102");
    fireEvent.click(screen.getByTestId("ticket-row-102"));
    const ctx = JSON.parse(window.sessionStorage.getItem("tiqora.ticketNavContext") ?? "null");
    expect(ctx.label).toBe("Eingang");
    expect(ctx.to).toBe("/agent/queues");
    expect(ctx.search).toMatchObject({ state_type: "pending" });
    expect(ctx.ids).toEqual([101, 102, 103]);
  });

  it("a row click still opens the ticket while something is selected", async () => {
    const router = await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");
    fireEvent.click(screen.getByTestId("queue-row-check-101"));

    fireEvent.click(screen.getByTestId("ticket-row-102"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/tickets/102"));
  });

  it("shift-click selects the range between two checkboxes", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    fireEvent.click(screen.getByTestId("queue-row-check-101"));
    fireEvent.click(screen.getByTestId("queue-row-check-103"), { shiftKey: true });

    expect(screen.getByTestId("queue-row-check-101")).toBeChecked();
    expect(screen.getByTestId("queue-row-check-102")).toBeChecked();
    expect(screen.getByTestId("queue-row-check-103")).toBeChecked();
  });

  it("Escape clears the selection", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");
    fireEvent.click(screen.getByTestId("queue-row-check-101"));
    expect(screen.getByTestId("queue-select-banner")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByTestId("queue-select-banner")).toBeNull();
    });
    expect(screen.getByTestId("queue-row-check-101")).not.toBeChecked();
  });

  it("header checkbox selects all loaded rows on the page", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    fireEvent.click(screen.getByTestId("queue-select-all-page"));

    expect(screen.getByTestId("queue-row-check-101")).toBeChecked();
    expect(screen.getByTestId("queue-row-check-102")).toBeChecked();
    expect(screen.getByTestId("queue-row-check-103")).toBeChecked();
    expect(screen.getByTestId("queue-selected-count").textContent).toMatch(/3/);
  });

  it("select-all-matches fetches remaining ids across pages and updates the banner", async () => {
    const bigTotal = 250;
    const firstPage = Array.from({ length: 3 }, (_, i) => makeTicket({ id: 100 + i }));
    listTickets.mockImplementation(async (params: ListParams = {}) => {
      if (params.escalating_within != null) return page([]);
      const offset = params.offset ?? 0;
      const limit = params.limit ?? 50;
      // The page's own list query always asks for the default page limit (50);
      // the "select all matches" id-fetch walks SELECT_ALL_PAGE_SIZE (200) pages.
      if (limit === 50) return page(firstPage, bigTotal);
      const end = Math.min(offset + limit, bigTotal);
      const items = [];
      for (let i = offset; i < end; i++) items.push(makeTicket({ id: 1000 + i }));
      return page(items, bigTotal);
    });

    await renderQueuesPage();
    await screen.findByTestId("ticket-row-100");
    fireEvent.click(screen.getByTestId("queue-row-check-100"));
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

    fireEvent.click(screen.getByTestId("queue-row-check-101"));
    fireEvent.click(screen.getByTestId("queue-row-check-102"));

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
    // Selection cleared: the status message stays, the selection part is gone.
    expect(screen.queryByTestId("queue-selected-count")).toBeNull();
  });

  it("partial failure keeps failed ids selected and reports a partial-fail status", async () => {
    patchTicket.mockImplementation(async (id: number) => {
      if (id === 102) throw new Error("boom");
    });

    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    fireEvent.click(screen.getByTestId("queue-row-check-101"));
    fireEvent.click(screen.getByTestId("queue-row-check-102"));

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

  it("the ✕ button clears the selection and hides the action bar", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");
    fireEvent.click(screen.getByTestId("queue-row-check-101"));

    fireEvent.click(screen.getByTestId("queue-select-clear"));

    await waitFor(() => expect(screen.queryByTestId("queue-select-banner")).toBeNull());
  });
});

describe("QueuesPage row quick edit", () => {
  beforeEach(resetMocks);

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

  it("row quick edit stays available while rows are selected", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    fireEvent.click(screen.getByTestId("queue-row-check-101"));

    expect(screen.getByTestId("ticket-row-state-101")).toBeInTheDocument();
    expect(screen.getByTestId("ticket-row-owner-101")).toBeInTheDocument();
  });
});

describe("QueuesPage customer filter", () => {
  beforeEach(() => {
    listQueues.mockReset();
    listTickets.mockReset();
    patchTicket.mockReset();
    listReferenceStates.mockReset();
    listReferencePriorities.mockReset();
    listReferenceAgents.mockReset();
    void i18n.changeLanguage("de");

    listQueues.mockResolvedValue([]);
    listReferenceStates.mockResolvedValue([]);
    listReferencePriorities.mockResolvedValue([]);
    listReferenceAgents.mockResolvedValue([]);
    patchTicket.mockResolvedValue(undefined);
  });

  it("passes customer_id from the URL to listTickets", async () => {
    listTickets.mockResolvedValue(page(tickets, tickets.length));
    await renderQueuesPage({ customer_id: "10042" });
    await screen.findByTestId("ticket-row-101");
    expect(listTickets).toHaveBeenCalledWith(
      expect.objectContaining({ customer_id: "10042" }),
    );
  });

  it("omits the chip when no customer_id filter is active", async () => {
    listTickets.mockResolvedValue(page(tickets, tickets.length));
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");
    expect(screen.queryByTestId("queue-customer-filter-chip")).toBeNull();
    // ...and the filter is not sent to the API either.
    expect(listTickets).toHaveBeenCalledWith(
      expect.objectContaining({ customer_id: undefined }),
    );
  });

  it("shows an active filter chip that clears the filter when dismissed", async () => {
    listTickets.mockResolvedValue(page(tickets, tickets.length));
    const router = await renderQueuesPage({ customer_id: "10042" });
    await screen.findByTestId("ticket-row-101");

    const chip = screen.getByTestId("queue-customer-filter-chip");
    expect(chip).toHaveTextContent("10042");

    fireEvent.click(screen.getByTestId("queue-customer-filter-clear"));

    await waitFor(() => {
      expect(screen.queryByTestId("queue-customer-filter-chip")).toBeNull();
    });
    const search = router.state.location.search as Record<string, unknown>;
    expect(search.customer_id).toBeUndefined();
  });

  it("clicking a ticket's customer cell sets the customer_id search param without navigating away", async () => {
    const withCustomer = [
      makeTicket({ id: 101, customer_id: "10042", customer_user_id: "bob" }),
      makeTicket({ id: 102 }),
    ];
    listTickets.mockResolvedValue(page(withCustomer, withCustomer.length));
    const router = await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    fireEvent.click(screen.getByTestId("ticket-customer-name-101"));

    await waitFor(() => {
      const search = router.state.location.search as Record<string, unknown>;
      expect(search.customer_id).toBe("10042");
    });
    expect(router.state.location.pathname).toBe("/agent/queues");
  });
});

describe("QueuesPage more-actions menu", () => {
  beforeEach(resetMocks);

  it("hides the archived toggle for non-admins but still offers the CSV export", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");
    fireEvent.click(screen.getByTestId("queue-more-actions"));

    await screen.findByTestId("queue-export-csv");
    expect(screen.queryByTestId("queue-show-archived")).toBeNull();
  });

  it("shows the archived toggle for admins and passes include_archived to the API", async () => {
    authUser.current = { id: 5, login: "agent1", is_admin: true };
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    fireEvent.click(screen.getByTestId("queue-more-actions"));
    fireEvent.click(await screen.findByTestId("queue-show-archived"));

    await waitFor(() => {
      expect(listTickets).toHaveBeenCalledWith(
        expect.objectContaining({ include_archived: true }),
      );
    });
  });

  it("exports the current view as CSV", async () => {
    const assign = vi.spyOn(window, "location", "get");
    const loc = { href: "" } as Location;
    assign.mockReturnValue(loc);
    await renderQueuesPage({ state_type: "pending" });
    await screen.findByTestId("ticket-row-101");

    fireEvent.click(screen.getByTestId("queue-more-actions"));
    fireEvent.click(await screen.findByTestId("queue-export-csv"));

    expect(exportTicketsCsvUrl).toHaveBeenCalledWith(
      expect.objectContaining({ state_type: "pending", sort: "activity" }),
    );
    expect(loc.href).toBe("/export.csv");
    assign.mockRestore();
  });
});

describe("QueuesPage status segments and flag chips", () => {
  beforeEach(resetMocks);

  it("defaults to the “Zu tun” segment, sorted by latest activity", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    expect(screen.getByTestId("queue-state-tab-todo")).toHaveAttribute("aria-selected", "true");
    expect(listTickets).toHaveBeenCalledWith(
      expect.objectContaining({ state_type: "todo", sort: "activity", order: "desc" }),
    );
    expect(screen.getByTestId("queue-meta-line")).toHaveTextContent("nach letzter Aktivität");
  });

  it("shows facet counts on the segments and chips", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    await waitFor(() => expect(screen.getByTestId("queue-state-tab-pending")).toHaveTextContent("4"));
    expect(screen.getByTestId("queue-state-tab-open")).toHaveTextContent("2");
    expect(screen.getByTestId("queue-flag-escalated")).toHaveTextContent("1");
    // A flag with no matches is disabled rather than offering an empty list.
    expect(screen.getByTestId("queue-flag-locked")).toBeDisabled();
  });

  it("the “Offen” segment asks for the literal open state type", async () => {
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    fireEvent.click(screen.getByTestId("queue-state-tab-open"));

    await waitFor(() =>
      expect(listTickets).toHaveBeenCalledWith(
        expect.objectContaining({ state_type: "open_only", offset: 0 }),
      ),
    );
  });

  it("flag chips toggle combinable filters and can be reset", async () => {
    const router = await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    fireEvent.click(screen.getByTestId("queue-flag-unassigned"));
    await waitFor(() =>
      expect(listTickets).toHaveBeenCalledWith(expect.objectContaining({ unassigned: true })),
    );
    expect(screen.getByTestId("queue-flag-unassigned")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByTestId("queue-flag-escalated"));
    await waitFor(() =>
      expect(listTickets).toHaveBeenCalledWith(
        expect.objectContaining({ unassigned: true, escalated: true }),
      ),
    );

    fireEvent.click(screen.getByTestId("queue-flag-reset"));
    await waitFor(() => {
      const search = router.state.location.search as Record<string, unknown>;
      expect(search.unassigned).toBeUndefined();
      expect(search.escalated).toBeUndefined();
    });
  });

  it("channel chips show once a chat channel has tickets, combine, and reset with the flags", async () => {
    ticketFacets.mockResolvedValue({ ...FACETS, channels: { email: 12, telegram: 3, webchat: 0 } });
    const router = await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");

    const telegram = await screen.findByTestId("queue-channel-telegram");
    expect(telegram).toHaveTextContent("3");
    // Web chat has no tickets yet — no chip for it.
    expect(screen.queryByTestId("queue-channel-webchat")).toBeNull();

    fireEvent.click(telegram);
    await waitFor(() =>
      expect(listTickets).toHaveBeenCalledWith(expect.objectContaining({ channel: ["telegram"], offset: 0 })),
    );
    fireEvent.click(screen.getByTestId("queue-channel-email"));
    await waitFor(() =>
      expect(listTickets).toHaveBeenCalledWith(expect.objectContaining({ channel: ["telegram", "email"] })),
    );

    fireEvent.click(screen.getByTestId("queue-flag-reset"));
    await waitFor(() => {
      const search = router.state.location.search as Record<string, unknown>;
      expect(search.channel).toBeUndefined();
    });
  });

  it("shows no channel chips while every ticket is an e-mail", async () => {
    ticketFacets.mockResolvedValue({ ...FACETS, channels: { email: 16, telegram: 0, webchat: 0 } });
    await renderQueuesPage();
    await screen.findByTestId("ticket-row-101");
    await waitFor(() => expect(ticketFacets).toHaveBeenCalled());
    expect(screen.queryByTestId("queue-channel-chips")).toBeNull();
  });

  it("shows a Telegram row's channel pill, chat name and edge colour", async () => {
    serveTickets([
      makeTicket({ id: 301, channel: "telegram", chat_display_name: "Kim", chat_username: "kim_example", customer_id: "tg-guest" }),
      makeTicket({ id: 302 }),
    ]);
    await renderQueuesPage();
    const row = await screen.findByTestId("ticket-row-301");
    expect(screen.getByTestId("ticket-channel-301")).toHaveAttribute("data-channel", "telegram");
    expect(screen.getByTestId("ticket-chat-identity-301")).toHaveTextContent("Kim @kim_example");
    expect(screen.queryByTestId("ticket-customer-name-301")).toBeNull();
    expect(row.style.getPropertyValue("--spine-color")).toBe("var(--color-channel-telegram)");
    expect(screen.getByTestId("ticket-channel-302")).toHaveAttribute("data-channel", "email");
  });

  it("pins overdue / due-soon tickets above the list, nearest deadline first", async () => {
    const now = Math.floor(Date.now() / 1000);
    const soon = makeTicket({ id: 201, title: "Soon", escalation_solution_time: now + 600 });
    const overdue = makeTicket({ id: 202, title: "Overdue", escalation_response_time: now - 600 });
    // The overdue ticket is also on the regular page — it must only render once.
    serveTickets([...tickets, overdue], [overdue, soon]);

    await renderQueuesPage();
    await screen.findByTestId("ticket-table-pinned-head");

    // The backend picks the most urgent ones: sorting by activity first let
    // the most overdue (least active) tickets fall outside the fetched page.
    expect(listTickets).toHaveBeenCalledWith(
      expect.objectContaining({
        escalating_within: 7200,
        state_type: "todo",
        sort: "deadline",
        order: "asc",
      }),
    );
    const rows = screen.getAllByTestId(/^ticket-row-\d+$/).map((r) => r.dataset.testid);
    expect(rows.slice(0, 2)).toEqual(["ticket-row-202", "ticket-row-201"]);
    expect(rows.filter((r) => r === "ticket-row-202")).toHaveLength(1);
  });

  it("“show all” lists every pinned ticket, not only the overdue ones", async () => {
    const now = Math.floor(Date.now() / 1000);
    const pinned = Array.from({ length: 5 }, (_, i) =>
      makeTicket({ id: 300 + i, title: `Due ${i}`, escalation_solution_time: now + 60 * (i + 1) }),
    );
    listTickets.mockImplementation(async (params: ListParams = {}) =>
      params.escalating_within != null ? page(pinned, 12) : page(tickets),
    );

    const router = await renderQueuesPage();
    fireEvent.click(await screen.findByTestId("ticket-table-pinned-show-all"));

    await waitFor(() => {
      const search = router.state.location.search as Record<string, unknown>;
      expect(search.sort).toBe("deadline");
      expect(search.order).toBe("asc");
      expect(search.escalated).toBeUndefined();
    });
  });

  it("does not pin anything while the “Eskaliert” chip is on", async () => {
    await renderQueuesPage({ escalated: "true" });
    await screen.findByTestId("ticket-row-101");

    expect(listTickets).not.toHaveBeenCalledWith(
      expect.objectContaining({ escalating_within: expect.any(Number) }),
    );
    expect(screen.queryByTestId("ticket-table-pinned-head")).toBeNull();
  });
});
