import { describe, it, expect, vi } from "vitest";
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
import { AdminCommandPalette } from "./AdminCommandPalette";

/**
 * Renders the palette inside a minimal in-memory router (needs `useNavigate`)
 * with a catch-all child route so any `/admin/...` target resolves without
 * registering all 36 admin routes individually.
 */
async function renderPalette(open: boolean, onClose = vi.fn()) {
  const rootRoute = createRootRoute({
    component: () => <AdminCommandPalette open={open} onClose={onClose} />,
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
  return { router, onClose };
}

describe("AdminCommandPalette", () => {
  it("renders nothing when closed", async () => {
    await renderPalette(false);
    expect(screen.queryByTestId("admin-search-input")).not.toBeInTheDocument();
  });

  it("filters by a keyword synonym (smtp -> mail outbound) and lists it above unrelated pages", async () => {
    await renderPalette(true);
    const input = screen.getByTestId("admin-search-input");
    fireEvent.change(input, { target: { value: "smtp" } });

    expect(screen.getByTestId("admin-search-result-mail-outbound")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-search-result-users")).not.toBeInTheDocument();
  });

  it("navigates with ArrowDown + Enter", async () => {
    const { router, onClose } = await renderPalette(true);
    const input = screen.getByTestId("admin-search-input");
    fireEvent.change(input, { target: { value: "queue" } });

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    await waitFor(() => expect(router.state.location.pathname).not.toBe("/admin"));
  });

  it("clicking a result navigates and closes", async () => {
    const { router, onClose } = await renderPalette(true);
    fireEvent.change(screen.getByTestId("admin-search-input"), { target: { value: "2fa" } });

    fireEvent.click(screen.getByTestId("admin-search-result-auth-config"));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/auth-config"));
  });

  it("shows the no-results state for a query matching nothing", async () => {
    await renderPalette(true);
    fireEvent.change(screen.getByTestId("admin-search-input"), {
      target: { value: "zzz-nonexistent" },
    });

    expect(screen.getByText(i18n.t("admin.commandPalette.noResults"))).toBeInTheDocument();
  });

  it("Escape closes the palette", async () => {
    const { onClose } = await renderPalette(true);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
