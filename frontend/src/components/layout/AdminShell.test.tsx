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
  it("renders the icon rail with one button per group and only the active group's pages", async () => {
    await renderShell("/admin/queues");
    expect(screen.getByTestId("admin-nav-rail")).toBeInTheDocument();
    for (const group of ["access", "tickets", "communication", "ai", "automation", "system"]) {
      expect(screen.getByTestId(`admin-rail-${group}`)).toBeInTheDocument();
    }
    // Route /admin/queues belongs to "tickets": its pages are listed …
    expect(screen.getByTestId("admin-nav-queues")).toBeInTheDocument();
    expect(screen.getByTestId("admin-nav-states")).toBeInTheDocument();
    // … while other groups' pages are not rendered in the context column.
    expect(screen.queryByTestId("admin-nav-users")).toBeNull();
    expect(screen.getByTestId("admin-rail-tickets").getAttribute("aria-pressed")).toBe("true");
  });

  it("switches the context column when another rail group is clicked", async () => {
    await renderShell("/admin/queues");
    fireEvent.click(screen.getByTestId("admin-rail-ai"));
    expect(screen.getByTestId("admin-nav-ai-queues")).toBeInTheDocument();
    expect(screen.getByTestId("admin-nav-ai-acl")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-nav-queues")).toBeNull();
  });

  it("collapses and re-expands the context column", async () => {
    await renderShell("/admin/queues");
    expect(screen.getByTestId("admin-nav-context")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("admin-nav-collapse"));
    expect(screen.queryByTestId("admin-nav-context")).toBeNull();
    // Clicking a rail group re-opens the column.
    fireEvent.click(screen.getByTestId("admin-rail-access"));
    expect(screen.getByTestId("admin-nav-context")).toBeInTheDocument();
    localStorage.removeItem("tiqora.admin.nav.collapsed");
  });

  it("marks the current route's nav entry active", async () => {
    await renderShell("/admin/queues");
    const link = screen.getByTestId("admin-nav-queues");
    expect(link.className).toContain("text-accent");
  });

  it("keeps every registered admin page reachable via the mobile nav", async () => {
    await renderShell();
    fireEvent.click(screen.getByTestId("admin-sidebar-toggle"));
    for (const page of ADMIN_PAGES) {
      expect(screen.getByTestId(`admin-nav-mobile-${page.slug}`)).toBeInTheDocument();
    }
  });

  it("opens the command palette from the sidebar search trigger", async () => {
    await renderShell();
    expect(screen.queryByTestId("admin-search-input")).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByTestId("admin-search-trigger")[0]);

    expect(screen.getByTestId("admin-search-input")).toBeInTheDocument();
  });

  it("opens the command palette on Ctrl+K", async () => {
    await renderShell();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByTestId("admin-search-input")).toBeInTheDocument();
  });
});
