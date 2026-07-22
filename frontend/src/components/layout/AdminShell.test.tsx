import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
} from "@tanstack/react-router";
import i18n from "@/i18n";
import { AdminShell } from "./AdminShell";
import { ADMIN_PAGES } from "@/lib/adminSearch";

vi.mock("@/components/agent/AccountMenu", () => ({
  AccountMenu: () => <div data-testid="account-menu-stub" />,
}));

async function renderShell(initialPath = "/admin") {
  const rootRoute = createRootRoute({
    component: () => (
      <AdminShell>
        <div data-testid="admin-shell-content" />
      </AdminShell>
    ),
  });
  // One child route per admin page (plus the two header links) so Link's
  // active-state matching behaves like it does against the real router,
  // instead of relying on a catch-all that would match everything.
  const pageRoutes = ADMIN_PAGES.map((page) =>
    createRoute({ getParentRoute: () => rootRoute, path: page.route, component: () => null }),
  );
  const agentRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/agent",
    component: () => null,
  });
  const agentSearchRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/agent/search",
    component: () => null,
  });
  const adminIndexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/admin",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([...pageRoutes, agentRoute, agentSearchRoute, adminIndexRoute]),
    history: createMemoryHistory({ initialEntries: [initialPath] }),
  });
  await router.load();
  render(
    <I18nextProvider i18n={i18n}>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      <RouterProvider router={router as any} />
    </I18nextProvider>,
  );
  return router;
}

describe("AdminShell", () => {
  it("renders all 5 domain groups with every registered admin page as a nav entry", async () => {
    await renderShell();
    expect(screen.getByTestId("admin-sidebar-nav")).toBeInTheDocument();
    for (const page of ADMIN_PAGES) {
      expect(screen.getByTestId(`admin-nav-${page.slug}`)).toBeInTheDocument();
    }
    expect(screen.getByText(i18n.t("admin.group.access"))).toBeInTheDocument();
    expect(screen.getByText(i18n.t("admin.group.tickets"))).toBeInTheDocument();
    expect(screen.getByText(i18n.t("admin.group.communication"))).toBeInTheDocument();
    expect(screen.getByText(i18n.t("admin.group.automation"))).toBeInTheDocument();
    expect(screen.getByText(i18n.t("admin.group.system"))).toBeInTheDocument();
  });

  it("marks the current route's nav entry active", async () => {
    await renderShell("/admin/queues");
    const link = screen.getByTestId("admin-nav-queues");
    expect(link.className).toContain("text-accent");
  });

  it("opens the command palette from the sidebar search trigger", async () => {
    await renderShell();
    expect(screen.queryByTestId("admin-search-input")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-search-trigger"));

    expect(screen.getByTestId("admin-search-input")).toBeInTheDocument();
  });

  it("opens the command palette on Ctrl+K", async () => {
    await renderShell();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByTestId("admin-search-input")).toBeInTheDocument();
  });
});
