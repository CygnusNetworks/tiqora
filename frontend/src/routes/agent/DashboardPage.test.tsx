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
import { DashboardPage } from "./DashboardPage";

const { listQueues, dashboardSummary, listTickets } = vi.hoisted(() => ({
  listQueues: vi.fn(),
  dashboardSummary: vi.fn(),
  listTickets: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: { listQueues, dashboardSummary, listTickets },
  };
});

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 5, login: "agent1", first_name: "Ada", last_name: "Agent", email: "a@example.com" },
  }),
}));

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
    ai_paused: false,
    archive_flag: 0,
    ...overrides,
  };
}

function page(items: TicketListItem[]) {
  return { items, limit: 50, offset: 0, total: items.length };
}

const mineOpen = [makeTicket({ id: 101 }), makeTicket({ id: 102 })];
const mineNew = [makeTicket({ id: 201, state_type: "new", state: "new" })];
const unowned = [
  makeTicket({ id: 301, owner_id: 1, state_type: "new", state: "new" }),
  makeTicket({ id: 302, owner_id: 1, state_type: "new", state: "new" }),
  makeTicket({ id: 303, owner_id: 1, state_type: "new", state: "new" }),
];
const pastEpoch = Math.floor(Date.now() / 1000) - 3600;
const escalatedTicket = makeTicket({ id: 401, owner_id: 9, escalation_time: pastEpoch });

function setupApi({ escalated = 1 }: { escalated?: number } = {}) {
  listQueues.mockResolvedValue([]);
  dashboardSummary.mockResolvedValue({
    my_open: mineOpen.length,
    my_new: mineNew.length,
    unowned_new: unowned.length,
    escalated,
  });
  listTickets.mockImplementation((params: Record<string, unknown>) => {
    if (params.owner_id === 5 && params.state_type === "open") return Promise.resolve(page(mineOpen));
    if (params.owner_id === 5 && params.state_type === "new") return Promise.resolve(page(mineNew));
    if (params.owner_id === 1 && params.state_type === "new") return Promise.resolve(page(unowned));
    if (params.owner_id === undefined && params.state_type === "open")
      return Promise.resolve(page(escalated > 0 ? [escalatedTicket] : []));
    if (params.owner_id === 5 && params.state_type === "pending") return Promise.resolve(page([]));
    return Promise.resolve(page([]));
  });
}

async function renderDashboard() {
  const rootRoute = createRootRoute({ component: () => <DashboardPage /> });
  const ticketRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/tickets/$ticketId" });
  const queuesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/agent/queues",
    validateSearch: (s: Record<string, unknown>) => s,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([ticketRoute, queuesRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
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
}

describe("DashboardPage", () => {
  beforeEach(() => {
    listQueues.mockReset();
    dashboardSummary.mockReset();
    listTickets.mockReset();
    window.localStorage.clear();
    void i18n.changeLanguage("de");
  });

  it("renders chip counts and the grouped view (with group headers) by default", async () => {
    setupApi();
    await renderDashboard();

    expect(await screen.findByTestId("dashboard-chip-all")).toHaveTextContent("6");
    expect(screen.getByTestId("dashboard-chip-mine-open")).toHaveTextContent("2");
    expect(screen.getByTestId("dashboard-chip-mine-new")).toHaveTextContent("1");
    expect(screen.getByTestId("dashboard-chip-takeover")).toHaveTextContent("3");
    expect(screen.getByTestId("dashboard-chip-all")).toHaveAttribute("aria-pressed", "true");

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-group-escalated")).toBeInTheDocument();
    });
    expect(screen.getByTestId("dashboard-group-mine-new")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-group-mine-open")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-group-takeover")).toBeInTheDocument();
  });

  it("filters the list to a flat view when a chip is clicked, no group headers", async () => {
    setupApi();
    await renderDashboard();
    await screen.findByTestId("dashboard-chip-all");

    fireEvent.click(screen.getByTestId("dashboard-chip-mine-open"));

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-chip-mine-open")).toHaveAttribute("aria-pressed", "true");
    });
    expect(screen.queryByTestId("dashboard-group-mine-open")).not.toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-group-takeover")).not.toBeInTheDocument();
    expect(screen.getByTestId("dashboard-ticket-101")).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-ticket-301")).not.toBeInTheDocument();
  });

  it("toggles the active chip back to Alle when clicked again", async () => {
    setupApi();
    await renderDashboard();
    await screen.findByTestId("dashboard-chip-all");

    fireEvent.click(screen.getByTestId("dashboard-chip-takeover"));
    await waitFor(() =>
      expect(screen.getByTestId("dashboard-chip-takeover")).toHaveAttribute("aria-pressed", "true"),
    );

    fireEvent.click(screen.getByTestId("dashboard-chip-takeover"));
    await waitFor(() =>
      expect(screen.getByTestId("dashboard-chip-all")).toHaveAttribute("aria-pressed", "true"),
    );
    expect(screen.getByTestId("dashboard-group-takeover")).toBeInTheDocument();
  });

  it("shows the escalation strip only when there are escalated tickets, and switches the list on click", async () => {
    setupApi({ escalated: 0 });
    await renderDashboard();
    await screen.findByTestId("dashboard-chip-all");
    expect(screen.queryByTestId("dashboard-escalation-strip")).not.toBeInTheDocument();
  });

  it("switches to the escalated view via the strip without hiding it", async () => {
    setupApi({ escalated: 1 });
    await renderDashboard();
    const strip = await screen.findByTestId("dashboard-escalation-strip");
    expect(strip).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("dashboard-escalation-strip-show"));

    await waitFor(() => {
      expect(screen.queryByTestId("dashboard-group-mine-new")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("dashboard-escalation-strip")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-ticket-401")).toBeInTheDocument();
  });
});
