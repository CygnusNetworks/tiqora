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
import { AgentShell } from "./AgentShell";

// Header widgets pull in their own api/SSE machinery that's out of scope for
// the sidebar tests here — stub them so the shell mounts cheaply.
vi.mock("@/lib/useSSE", () => ({
  SSEProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/agent/NotificationBell", () => ({
  NotificationBell: () => <div data-testid="notification-bell-stub" />,
  NotificationToaster: () => null,
}));
vi.mock("@/components/agent/CommandSearch", () => ({
  CommandSearch: () => <div data-testid="command-search-stub" />,
}));
vi.mock("@/components/agent/NewTicketButton", () => ({
  NewTicketButton: () => <div data-testid="new-ticket-button-stub" />,
}));
vi.mock("@/components/agent/ConnectionStatus", () => ({
  ConnectionStatus: () => <div data-testid="connection-status-stub" />,
}));
vi.mock("@/components/agent/OnlineAgentsPopover", () => ({
  OnlineAgentsPopover: () => <div data-testid="online-agents-stub" />,
}));
vi.mock("@/components/agent/AccountMenu", () => ({
  AccountMenu: () => <div data-testid="account-menu-stub" />,
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 1, login: "agent", can_edit_templates: false } }),
}));

const { listQueues, myTicketCounts, dashboardSummary } = vi.hoisted(() => ({
  listQueues: vi.fn(),
  myTicketCounts: vi.fn(),
  dashboardSummary: vi.fn(),
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { listQueues, myTicketCounts, dashboardSummary } };
});

async function renderShell(initialEntry = "/agent") {
  const rootRoute = createRootRoute({
    component: () => (
      <AgentShell>
        <div data-testid="agent-shell-content" />
      </AgentShell>
    ),
  });
  const childPaths = ["/agent", "/agent/queues", "/agent/kb", "/agent/calendar", "/agent/stats", "/agent/search"];
  const childRoutes = childPaths.map((path) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path,
      component: () => null,
      validateSearch: (s: Record<string, unknown>) => s,
    }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren(childRoutes),
    history: createMemoryHistory({ initialEntries: [initialEntry] }),
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

describe("AgentShell sidebar", () => {
  beforeEach(() => {
    listQueues.mockReset().mockResolvedValue([]);
    myTicketCounts.mockReset().mockResolvedValue({ open: 0, new: 0 });
    dashboardSummary.mockReset().mockResolvedValue({
      my_open: 0,
      my_new: 0,
      unowned_new: 0,
      escalated: 0,
      ai_escalated: 0,
    });
    window.localStorage.clear();
    void i18n.changeLanguage("de");
  });

  it("renders all groups expanded by default", async () => {
    await renderShell();
    expect(await screen.findByTestId("agent-sidebar-nav")).toBeInTheDocument();
    expect(screen.getByTestId("agent-nav-inbox")).toBeInTheDocument();
    expect(screen.getByTestId("agent-nav-kb")).toBeInTheDocument();
    expect(screen.getByTestId("agent-nav-calendar")).toBeInTheDocument();
    expect(screen.getByTestId("agent-nav-stats")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-group-workspace-toggle")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("collapses and expands a group on click", async () => {
    await renderShell();
    await screen.findByTestId("agent-sidebar-nav");
    const toggle = screen.getByTestId("sidebar-group-knowledge-toggle");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("agent-nav-kb")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("agent-nav-kb")).toBeInTheDocument();
  });

  it("persists collapsed state across remounts", async () => {
    await renderShell();
    await screen.findByTestId("agent-sidebar-nav");
    fireEvent.click(screen.getByTestId("sidebar-group-insights-toggle"));
    await waitFor(() =>
      expect(screen.getByTestId("sidebar-group-insights-toggle")).toHaveAttribute(
        "aria-expanded",
        "false",
      ),
    );

    await renderShell();
    const toggles = await screen.findAllByTestId("sidebar-group-insights-toggle");
    // Two AgentShell instances are now mounted (desktop aside from each
    // render); the freshly-mounted one reads the persisted collapsed state.
    expect(toggles.at(-1)).toHaveAttribute("aria-expanded", "false");
  });

  it("still supports the queue search and show-all toggle inside its group", async () => {
    await renderShell();
    await screen.findByTestId("agent-sidebar-nav");
    expect(screen.getByTestId("sidebar-queue-search")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-queues-toggle-all")).toBeInTheDocument();
  });

  it("lists who-owns-it views only — locked/escalated moved to the list's filter chips", async () => {
    await renderShell();
    await screen.findByTestId("agent-sidebar-nav");
    expect(screen.getByTestId("agent-nav-my-tickets")).toBeInTheDocument();
    expect(screen.getByTestId("agent-nav-watched")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-nav-locked")).toBeNull();
    expect(screen.queryByTestId("agent-nav-escalated")).toBeNull();
    // Every workspace entry carries an icon.
    expect(screen.getByTestId("agent-nav-inbox").querySelector("svg")).not.toBeNull();
  });

  it("marks only the current ticket view as active, not the inbox as well", async () => {
    await renderShell("/agent/queues?view=mine&owner_id=1");
    await screen.findByTestId("agent-sidebar-nav");
    expect(screen.getByTestId("agent-nav-my-tickets")).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("agent-nav-inbox")).not.toHaveAttribute("aria-current");
  });

  it("marks the inbox active on the plain ticket list", async () => {
    await renderShell("/agent/queues");
    await screen.findByTestId("agent-sidebar-nav");
    expect(screen.getByTestId("agent-nav-inbox")).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("agent-nav-my-tickets")).not.toHaveAttribute("aria-current");
  });

  it("shows the escalated count as a red pill on the inbox", async () => {
    dashboardSummary.mockResolvedValue({
      my_open: 0,
      my_new: 0,
      unowned_new: 0,
      escalated: 3,
      ai_escalated: 0,
    });
    await renderShell();
    const pill = await screen.findByTestId("nav-escalated-count");
    expect(pill).toHaveTextContent("3");
    expect(screen.getByTestId("agent-nav-inbox")).toContainElement(pill);
  });
});
