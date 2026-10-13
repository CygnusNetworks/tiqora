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
import { helloPlugin, navEntry, routePage } from "@/test/pluginFixtures";
import { AgentShell } from "./AgentShell";

// Header widgets pull in their own api/SSE machinery that's out of scope for
// the sidebar tests here — stub them so the shell mounts cheaply.
vi.mock("@/lib/useSSE", () => ({
  SSEProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/agent/phone/CallPopup", () => ({
  CallPopup: () => null,
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

const { frontend } = vi.hoisted(() => ({ frontend: vi.fn() }));
vi.mock("@/lib/pluginsApi", () => ({ pluginsApi: { frontend } }));

const authFlags = vi.hoisted(() => ({ canUseDirectory: false, canViewReports: true }));
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: {
      id: 1,
      login: "agent",
      can_edit_templates: false,
      can_use_customer_directory: authFlags.canUseDirectory,
      can_view_reports: authFlags.canViewReports,
      can_create_reports: authFlags.canViewReports,
      can_share_reports: false,
    },
  }),
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

const { listSearches, searchCounts } = vi.hoisted(() => ({ listSearches: vi.fn(), searchCounts: vi.fn() }));
vi.mock("@/lib/savedSearchesApi", () => ({
  savedSearchesApi: { list: listSearches, counts: searchCounts, setPrefs: vi.fn() },
}));
const { chatTopics } = vi.hoisted(() => ({ chatTopics: vi.fn() }));
vi.mock("@/lib/webchatApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/webchatApi")>("@/lib/webchatApi");
  return {
    ...actual,
    webchatApi: {
      ...actual.webchatApi,
      topics: chatTopics,
      getAgentState: vi
        .fn()
        .mockResolvedValue({ available: false, max_concurrent: 3, active_count: 0, topics: [] }),
      inbox: vi.fn().mockResolvedValue({ waiting: [], mine: [] }),
    },
  };
});

const { itsmStatus } = vi.hoisted(() => ({ itsmStatus: vi.fn() }));
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, status: itsmStatus } };
});

const statusOf = (mode: string, ciSource: "znuny" | "tiqora" | null) => ({
  mode,
  source: "auto",
  setup_complete: true,
  ci_available: ciSource !== null,
  ci_read_only: ciSource === "znuny",
  ci_source: ciSource,
  unavailable_code: null,
  znuny: { core_installed: true, cmdb_installed: true, profile: null, packages: {} },
});

async function renderShell(initialEntry = "/agent") {
  const rootRoute = createRootRoute({
    component: () => (
      <AgentShell>
        <div data-testid="agent-shell-content" />
      </AgentShell>
    ),
  });
  const childPaths = [
    "/agent",
    "/agent/queues",
    "/agent/searches/new",
    "/agent/searches/$searchId",
    "/agent/kb",
    "/agent/calendar",
    "/agent/reports",
    "/agent/search",
    "/agent/customers",
    "/agent/itsm/cis",
  ];
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
    frontend.mockReset().mockResolvedValue([]);
    sessionStorage.clear();
    listQueues.mockReset().mockResolvedValue([]);
    myTicketCounts.mockReset().mockResolvedValue({ open: 0, new: 0 });
    dashboardSummary.mockReset().mockResolvedValue({
      my_open: 0,
      my_new: 0,
      unowned_new: 0,
      escalated: 0,
      ai_escalated: 0,
    });
    listSearches.mockReset().mockResolvedValue([]);
    searchCounts.mockReset().mockResolvedValue({ counts: {} });
    chatTopics.mockReset().mockResolvedValue([]);
    itsmStatus.mockReset().mockResolvedValue(statusOf("off", null));
    window.localStorage.clear();
    authFlags.canUseDirectory = false;
    void i18n.changeLanguage("de");
  });

  it("shows an Erweiterungen group with plugin entries", async () => {
    i18n.addResourceBundle("de", "plugin.org.example.hello", { nav: { overview: "Hallo" } }, true, true);
    frontend.mockResolvedValue([
      helloPlugin({ locales: [], contributions: [routePage("overview", "overview"), navEntry("overview", "overview")] }),
    ]);
    await renderShell();
    expect(await screen.findByText("Erweiterungen")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Hallo" })).toHaveAttribute(
      "href",
      "/agent/plugins/org.example.hello/overview",
    );
  });

  it("has no Erweiterungen group without plugin entries", async () => {
    await renderShell();
    await screen.findByTestId("agent-sidebar-nav");
    await waitFor(() => expect(frontend).toHaveBeenCalled());
    expect(screen.queryByTestId("sidebar-group-plugins-toggle")).toBeNull();
  });

  it("hides the chat inbox button without chat topics", async () => {
    await renderShell();
    await screen.findByTestId("agent-sidebar-nav");
    await waitFor(() => expect(chatTopics).toHaveBeenCalled());
    expect(screen.queryByTestId("chat-inbox-trigger-desktop")).toBeNull();
  });

  it("shows the chat inbox button for an agent with chat topics", async () => {
    chatTopics.mockResolvedValue([{ ref: "1:support", label: "Support", queue_id: 1, config_name: "W" }]);
    await renderShell();
    expect(await screen.findByTestId("chat-inbox-trigger-desktop")).toBeInTheDocument();
    expect(screen.getByTestId("chat-inbox-trigger-mobile")).toBeInTheDocument();
  });

  it("shows the CMDB entry only for the Znuny read-through CIs", async () => {
    itsmStatus.mockResolvedValue(statusOf("znuny_readthrough", "znuny"));
    await renderShell();
    const link = await screen.findByTestId("agent-nav-cmdb");
    expect(link).toHaveAttribute("href", "/agent/itsm/cis");
    expect(link).toHaveTextContent("CMDB");
  });

  it.each([
    ["Tiqora CMDB (Mode B)", statusOf("tiqora", "tiqora")],
    ["ITSM off", statusOf("off", null)],
  ])("hides the CMDB entry for %s", async (_name, status) => {
    itsmStatus.mockResolvedValue(status);
    await renderShell();
    await screen.findByTestId("agent-sidebar-nav");
    await waitFor(() => expect(itsmStatus).toHaveBeenCalled());
    expect(screen.queryByTestId("agent-nav-cmdb")).toBeNull();
  });

  it("hides the CMDB entry when the status call fails", async () => {
    itsmStatus.mockRejectedValue(new Error("boom"));
    await renderShell();
    await screen.findByTestId("agent-sidebar-nav");
    await waitFor(() => expect(itsmStatus).toHaveBeenCalled());
    expect(screen.queryByTestId("agent-nav-cmdb")).toBeNull();
  });

  it("shows the customer directory only with its permission", async () => {
    await renderShell();
    await screen.findByTestId("agent-sidebar-nav");
    expect(screen.queryByTestId("agent-nav-customers")).toBeNull();
  });

  it("links the customer directory when the agent may use it", async () => {
    authFlags.canUseDirectory = true;
    await renderShell();
    const link = await screen.findByTestId("agent-nav-customers");
    expect(link).toHaveAttribute("href", "/agent/customers");
  });

  it("hides the reports entry from an agent without report access", async () => {
    authFlags.canViewReports = false;
    try {
      await renderShell();
      expect(await screen.findByTestId("agent-nav-calendar")).toBeInTheDocument();
      expect(screen.queryByTestId("agent-nav-stats")).toBeNull();
    } finally {
      authFlags.canViewReports = true;
    }
  });

  it("renders all groups expanded by default", async () => {
    await renderShell();
    expect(await screen.findByTestId("agent-sidebar-nav")).toBeInTheDocument();
    expect(screen.getByTestId("agent-nav-inbox")).toBeInTheDocument();
    expect(screen.getByTestId("agent-nav-kb")).toBeInTheDocument();
    expect(screen.getByTestId("agent-nav-calendar")).toBeInTheDocument();
    expect(screen.getByTestId("agent-nav-stats")).toBeInTheDocument();
    // "Berichte" opens the reports page (the old /agent/stats only redirects there).
    expect(screen.getByTestId("agent-nav-stats")).toHaveAttribute("href", "/agent/reports");
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

  it("hides Znuny's system queues behind a remembered toggle", async () => {
    window.localStorage.removeItem("tiqora.sidebar.showSystemQueues");
    const q = (id: number, name: string) => ({
      id,
      name,
      group_id: 1,
      valid: true,
      counts: { open: 2, new: 0, total: 2 },
      children: [],
    });
    listQueues.mockResolvedValue([q(3, "Junk"), q(1, "Postmaster"), q(2, "Raw"), q(10, "support")]);
    await renderShell();
    await screen.findByTestId("sidebar-queue-10");
    expect(screen.queryByTestId("sidebar-queue-3")).toBeNull();
    expect(screen.queryByTestId("sidebar-queue-1")).toBeNull();

    // A search for one finds it regardless.
    fireEvent.change(screen.getByTestId("sidebar-queue-search"), { target: { value: "junk" } });
    expect(screen.getByTestId("sidebar-queue-3")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("sidebar-queue-search"), { target: { value: "" } });

    const toggle = screen.getByTestId("sidebar-queues-toggle-system");
    expect(toggle).toHaveTextContent("Systemqueues einblenden (3)");
    fireEvent.click(toggle);
    expect(screen.getByTestId("sidebar-queue-3")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-queues-toggle-system")).toHaveTextContent(
      "Systemqueues ausblenden",
    );
    expect(window.localStorage.getItem("tiqora.sidebar.showSystemQueues")).toBe("1");
    window.localStorage.removeItem("tiqora.sidebar.showSystemQueues");
  });

  it("keeps the open system queue visible", async () => {
    window.localStorage.removeItem("tiqora.sidebar.showSystemQueues");
    listQueues.mockResolvedValue([
      { id: 2, name: "Raw", group_id: 1, valid: true, counts: { open: 1, new: 1, total: 1 }, children: [] },
    ]);
    await renderShell("/agent/queues?queue_id=2");
    expect(await screen.findByTestId("sidebar-queue-2")).toBeInTheDocument();
    expect(screen.queryByTestId("sidebar-queues-toggle-system")).toBeNull();
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

  it("renders the saved-search group between workspace and queues", async () => {
    await renderShell();
    const order = (await screen.findAllByTestId(/^sidebar-group-.+-toggle$/)).map((el) =>
      el.getAttribute("data-testid"),
    );
    expect(order.slice(0, 4)).toEqual([
      "sidebar-group-workspace-toggle",
      "sidebar-group-searches-toggle",
      "sidebar-group-queues-toggle",
      "sidebar-group-knowledge-toggle",
    ]);
    expect(screen.getByTestId("sidebar-searches-new")).toHaveAttribute("href", "/agent/searches/new");
  });

  it("collapses the saved-search group and hides its entries", async () => {
    listSearches.mockResolvedValue([
      { id: 1, name: "Meine VIP", show_in_sidebar: true, show_count: false, position: 0 },
    ]);
    await renderShell();
    expect(await screen.findByTestId("sidebar-search-1")).toHaveAttribute("href", "/agent/searches/1");
    fireEvent.click(screen.getByTestId("sidebar-group-searches-toggle"));
    expect(screen.queryByTestId("sidebar-search-1")).not.toBeInTheDocument();
  });
});
