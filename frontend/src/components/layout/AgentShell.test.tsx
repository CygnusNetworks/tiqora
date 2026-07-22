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

const { listQueues, myTicketCounts } = vi.hoisted(() => ({
  listQueues: vi.fn(),
  myTicketCounts: vi.fn(),
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { listQueues, myTicketCounts } };
});

async function renderShell() {
  const rootRoute = createRootRoute({
    component: () => (
      <AgentShell>
        <div data-testid="agent-shell-content" />
      </AgentShell>
    ),
  });
  const childPaths = ["/agent", "/agent/queues", "/agent/kb", "/agent/kb/categories", "/agent/calendar", "/agent/stats", "/agent/search"];
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
    history: createMemoryHistory({ initialEntries: ["/agent"] }),
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
    fireEvent.click(screen.getByTestId("sidebar-group-reports-toggle"));
    await waitFor(() =>
      expect(screen.getByTestId("sidebar-group-reports-toggle")).toHaveAttribute(
        "aria-expanded",
        "false",
      ),
    );

    await renderShell();
    const toggles = await screen.findAllByTestId("sidebar-group-reports-toggle");
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
});
