import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { api, type RoleOut } from "@/lib/api";
import { dashboardsApi } from "@/lib/dashboardsApi";
import { demoTemplate } from "@/demo/dashboardsMock";
import { makeUser } from "@/test/fireUser";
import { DashboardTemplatesPage } from "./DashboardTemplatesPage";

const user = makeUser();

const role = (id: number, name: string, valid_id = 1): RoleOut => ({
  id,
  name,
  comments: null,
  valid_id,
  create_time: "2026-01-01T00:00:00",
  change_time: "2026-01-01T00:00:00",
});

const spies = {
  list: vi.spyOn(dashboardsApi.templates, "list"),
  create: vi.spyOn(dashboardsApi.templates, "create"),
  update: vi.spyOn(dashboardsApi.templates, "update"),
  remove: vi.spyOn(dashboardsApi.templates, "remove"),
  // `api.adminRoles` is a getter (a new object per access), so the call is caught one level down.
  request: vi.spyOn(api, "request"),
};

const SUPPORT = demoTemplate({ id: 9, name: "Support", role_ids: [3], locked: true, widget_count: 11, sort: 0, version: 2 });

async function renderPage() {
  const rootRoute = createRootRoute();
  const page = createRoute({ getParentRoute: () => rootRoute, path: "/admin/dashboard-templates", component: DashboardTemplatesPage });
  const agent = createRoute({ getParentRoute: () => rootRoute, path: "/agent" });
  const router = createRouter({
    routeTree: rootRoute.addChildren([page, agent]),
    history: createMemoryHistory({ initialEntries: ["/admin/dashboard-templates"] }),
  });
  await act(async () => {
    await router.load();
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeAll(async () => {
  window.scrollTo = () => {};
  await i18n.changeLanguage("de");
});

beforeEach(() => {
  spies.list.mockResolvedValue([SUPPORT]);
  spies.create.mockResolvedValue(SUPPORT);
  spies.update.mockResolvedValue(SUPPORT);
  spies.remove.mockResolvedValue(undefined);
  spies.request.mockImplementation(async (method: string, path: string) => {
    if (method === "GET" && path === "/api/v1/admin/roles") {
      return { items: [role(2, "Admins"), role(3, "Agenten"), role(4, "Alt", 2)], total: 3, page: 1, page_size: 500 };
    }
    throw new Error(`unexpected ${method} ${path}`);
  });
});

afterEach(() => {
  cleanup();
  for (const spy of Object.values(spies)) spy.mockReset();
});

describe("DashboardTemplatesPage", () => {
  it("lists templates with roles, lock state and widget count; the empty state explains what templates are", async () => {
    await renderPage();
    const row = await screen.findByRole("row", { name: /Support/ });
    await waitFor(() => expect(within(row).getByText("Agenten")).toBeVisible());
    expect(within(row).getByText("Ja")).toBeVisible();
    expect(within(row).getByText("11")).toBeVisible();
    cleanup();
    spies.list.mockResolvedValue([]);
    await renderPage();
    expect(await screen.findByText(/Vorlagen legen fest, womit ein neues Dashboard für eine Rolle startet/)).toBeVisible();
  });

  it("create requires a name and a role, then posts the template", async () => {
    spies.list.mockResolvedValue([]);
    await renderPage();
    await user.click(await screen.findByRole("button", { name: "Neue Vorlage" }));
    await user.click(screen.getByRole("button", { name: "Anlegen" }));
    expect(screen.getByText("Bitte einen Namen eingeben.")).toBeVisible();
    expect(screen.getByText("Bitte mindestens eine Rolle wählen.")).toBeVisible();
    expect(spies.create).not.toHaveBeenCalled();
    // only valid roles are offered
    expect(screen.queryByRole("checkbox", { name: "Alt" })).toBeNull();
    await user.type(screen.getByLabelText("Name"), "Support");
    await user.click(screen.getByRole("checkbox", { name: "Agenten" }));
    await user.click(screen.getByRole("switch", { name: /verbindlich/ }));
    await user.click(screen.getByRole("button", { name: "Anlegen" }));
    await waitFor(() =>
      expect(spies.create).toHaveBeenCalledWith({ name: "Support", icon: null, role_ids: [3], locked: true, sort: 0 }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("edit saves with the template's version", async () => {
    await renderPage();
    await user.click(await screen.findByRole("button", { name: "Bearbeiten" }));
    expect(screen.getByRole("checkbox", { name: "Agenten" })).toBeChecked();
    expect(screen.getByRole("switch", { name: /verbindlich/ })).toBeChecked();
    await user.clear(screen.getByLabelText("Priorität"));
    await user.type(screen.getByLabelText("Priorität"), "5");
    await user.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() =>
      expect(spies.update).toHaveBeenCalledWith(9, { name: "Support", icon: null, role_ids: [3], locked: true, sort: 5 }, 2),
    );
  });

  it("delete asks first and 'Layout bearbeiten' opens the template in the editor", async () => {
    await renderPage();
    expect(await screen.findByRole("link", { name: "Layout bearbeiten" })).toHaveAttribute("href", "/agent?d=9");
    await user.click(screen.getByRole("button", { name: "Löschen" }));
    expect(spies.remove).not.toHaveBeenCalled();
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByText("Vorlage „Support“ löschen?")).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: "Löschen" }));
    await waitFor(() => expect(spies.remove).toHaveBeenCalledWith(9));
  });
});
