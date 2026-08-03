import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
} from "@tanstack/react-router";
import i18n from "@/i18n";
import { PortalShell } from "./PortalShell";

const { logout } = vi.hoisted(() => ({ logout: vi.fn() }));
vi.mock("@/auth/CustomerAuthContext", () => ({
  useCustomerAuth: () => ({
    customer: { login: "alice", first_name: "Alice", email: "alice@example.com" },
    logout,
  }),
}));

const { toggleTheme } = vi.hoisted(() => ({ toggleTheme: vi.fn() }));
vi.mock("@/themes/theme", () => ({
  useTheme: () => ({ theme: "dark", toggleTheme }),
}));

async function renderShell() {
  const rootRoute = createRootRoute({
    component: () => (
      <PortalShell>
        <div data-testid="portal-shell-content" />
      </PortalShell>
    ),
  });
  const childPaths = ["/portal", "/portal/tickets/new", "/portal/kb", "/portal/login"];
  const childRoutes = childPaths.map((path) =>
    createRoute({ getParentRoute: () => rootRoute, path, component: () => null }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren(childRoutes),
    history: createMemoryHistory({ initialEntries: ["/portal"] }),
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

describe("PortalShell", () => {
  beforeEach(() => {
    logout.mockReset().mockResolvedValue(undefined);
    toggleTheme.mockReset();
    void i18n.changeLanguage("en");
  });

  it("renders the nav links, brand and content", async () => {
    await renderShell();
    expect(screen.getByTestId("portal-home-link")).toBeInTheDocument();
    expect(screen.getByText("My tickets")).toBeInTheDocument();
    expect(screen.getByText("New ticket")).toBeInTheDocument();
    expect(screen.getByText("Help articles")).toBeInTheDocument();
    expect(screen.getByTestId("portal-shell-content")).toBeInTheDocument();
  });

  it("shows the current customer's name", async () => {
    await renderShell();
    const label = screen.getByTestId("portal-current-customer");
    expect(label).toHaveTextContent("Alice");
    expect(label).toHaveAttribute("title", "alice@example.com");
  });

  it("toggles the theme when the theme button is clicked", async () => {
    await renderShell();
    fireEvent.click(screen.getByText("☀"));
    expect(toggleTheme).toHaveBeenCalledTimes(1);
  });

  it("switches the language and persists the choice", async () => {
    await renderShell();
    const trigger = screen.getByTestId("portal-lang-select");
    expect(trigger).toHaveTextContent("EN");
    fireEvent.click(trigger);
    fireEvent.click(screen.getByText("Deutsch"));
    await waitFor(() => expect(i18n.language).toMatch(/^de/));
    expect(localStorage.getItem("tiqora-lang")).toBe("de");
  });

  it("logs out and navigates to the portal login page", async () => {
    const router = await renderShell();
    fireEvent.click(screen.getByTestId("portal-logout-btn"));

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(router.state.location.pathname).toBe("/portal/login"));
  });
});
