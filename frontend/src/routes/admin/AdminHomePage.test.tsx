import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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
import { AdminHomePage } from "./AdminHomePage";
import { ADMIN_PAGES, ADMIN_PAGE_GROUPS } from "@/lib/adminSearch";
import type { DaemonServiceOut } from "@/lib/api";

const adminUsersList = vi.fn();
const adminQueuesList = vi.fn();
const getDaemons = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    adminUsers: { list: (...args: unknown[]) => adminUsersList(...args) },
    adminQueues: { list: (...args: unknown[]) => adminQueuesList(...args) },
    getDaemons: (...args: unknown[]) => getDaemons(...args),
  },
}));

function daemon(overrides: Partial<DaemonServiceOut> = {}): DaemonServiceOut {
  return {
    slug: "poller",
    enabled: true,
    toggleable: false,
    schedule: "interval",
    interval_seconds: 15,
    interval_overridden: false,
    daily_at: null,
    last_run_at: "2026-07-19T10:00:00+00:00",
    last_ok_at: "2026-07-19T10:00:00+00:00",
    last_error: null,
    last_result: null,
    ...overrides,
  };
}

async function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute({
    component: () => (
      <QueryClientProvider client={qc}>
        <AdminHomePage />
      </QueryClientProvider>
    ),
  });
  const catchAllRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "$",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([catchAllRoute]),
    history: createMemoryHistory({ initialEntries: ["/admin"] }),
  });
  await router.load();
  render(
    <I18nextProvider i18n={i18n}>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      <RouterProvider router={router as any} />
    </I18nextProvider>,
  );
}

describe("AdminHomePage", () => {
  beforeEach(() => {
    adminUsersList.mockReset().mockResolvedValue({ items: [], total: 7, page: 1, page_size: 1 });
    adminQueuesList.mockReset().mockResolvedValue({ items: [], total: 4, page: 1, page_size: 1 });
    getDaemons.mockReset().mockResolvedValue({
      services: [daemon(), daemon({ slug: "postmaster", enabled: false })],
    });
  });

  it("renders KPI cards from the mocked admin endpoints, incl. daemons active/error summary", async () => {
    getDaemons.mockResolvedValue({
      services: [
        daemon(),
        daemon({ slug: "postmaster", enabled: true, last_error: "boom", last_ok_at: null }),
      ],
    });

    await renderPage();

    await waitFor(() => expect(screen.getByTestId("admin-kpi-agents")).toHaveTextContent("7"));
    expect(screen.getByTestId("admin-kpi-queues")).toHaveTextContent("4");
    await waitFor(() =>
      expect(screen.getByTestId("admin-kpi-daemons")).toHaveTextContent("2/2"),
    );
    expect(screen.getByTestId("admin-kpi-daemons")).toHaveTextContent("1 with errors");
  });

  it("renders every group and every registered page as a quick-access card", async () => {
    await renderPage();

    for (const group of ADMIN_PAGE_GROUPS) {
      expect(await screen.findByTestId(`admin-home-group-${group}`)).toBeInTheDocument();
    }
    for (const page of ADMIN_PAGES) {
      expect(screen.getByTestId(`admin-home-link-${page.slug}`)).toBeInTheDocument();
    }
  });

  it("opens the command palette from the dashboard's own search field", async () => {
    await renderPage();
    expect(screen.queryByTestId("admin-search-input")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-dashboard-search-trigger"));

    expect(screen.getByTestId("admin-search-input")).toBeInTheDocument();
  });
});
