import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { DashboardConflictError, dashboardKeys, dashboardsApi, type Dashboard } from "@/lib/dashboardsApi";
import { demoDashboard, demoTemplateUpdateHint } from "@/demo/dashboardsMock";
import { TemplateUpdateBanner } from "./TemplateUpdateBanner";

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function setup(over: Partial<Dashboard> = {}, props: { editing?: boolean } = {}) {
  const dashboard = demoDashboard({
    id: 7,
    version: 4,
    can_edit: true,
    source_template_id: 100,
    template_update: demoTemplateUpdateHint({ template_name: "Support-Team", new_widget_count: 2 }),
    ...over,
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(dashboardKeys.one(7), dashboard);
  const spy = vi.spyOn(client, "invalidateQueries");
  const ui = (
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <TemplateUpdateBanner dashboard={dashboard} editing={props.editing} />
      </I18nextProvider>
    </QueryClientProvider>
  );
  return { client, spy, dashboard, ...render(ui) };
}

describe("TemplateUpdateBanner", () => {
  it("tells the owner about the new template version with name and count", () => {
    setup();
    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent("Neue Version der Vorlage „Support-Team“: 2 neue Widgets.");
    expect(screen.getByRole("button", { name: "Übernehmen" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Ausblenden" })).toBeVisible();
  });

  it("uses the singular for one new widget", () => {
    setup({ template_update: demoTemplateUpdateHint({ new_widget_count: 1 }) });
    expect(screen.getByRole("status")).toHaveTextContent("1 neues Widget");
  });

  it("is hidden for viewers, without an update and in edit mode", () => {
    setup({ can_edit: false });
    expect(screen.queryByRole("status")).toBeNull();
    cleanup();
    setup({ template_update: null });
    expect(screen.queryByRole("status")).toBeNull();
    cleanup();
    setup({}, { editing: true });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("'Übernehmen' posts merge with the dashboard version and shows the new widgets", async () => {
    const { client, dashboard, spy } = setup();
    const merged = {
      ...dashboard,
      version: 5,
      template_update: null,
      widgets: [{ id: "demo-tu-sla", type_key: "core.sla-gauge", type_version: 1, title_override: null, settings: {}, layout: {}, pinned: false }],
    } as Dashboard;
    const apply = vi.spyOn(dashboardsApi, "applyTemplateUpdate").mockResolvedValue(merged);
    fireEvent.click(screen.getByRole("button", { name: "Übernehmen" }));
    await waitFor(() => expect(apply).toHaveBeenCalledWith(7, { action: "merge", version: 4 }));
    await waitFor(() => expect(client.getQueryData<Dashboard>(dashboardKeys.one(7))?.widgets).toHaveLength(1));
    expect(client.getQueryData<Dashboard>(dashboardKeys.one(7))?.template_update).toBeNull();
    expect(spy).toHaveBeenCalledWith({ queryKey: dashboardKeys.data(7) });
  });

  it("'Ausblenden' posts dismiss", async () => {
    const { client, dashboard } = setup();
    const apply = vi.spyOn(dashboardsApi, "applyTemplateUpdate").mockResolvedValue({ ...dashboard, version: 5, template_update: null });
    fireEvent.click(screen.getByRole("button", { name: "Ausblenden" }));
    await waitFor(() => expect(apply).toHaveBeenCalledWith(7, { action: "dismiss", version: 4 }));
    await waitFor(() => expect(client.getQueryData<Dashboard>(dashboardKeys.one(7))?.template_update).toBeNull());
  });

  it("a 409 reloads the dashboard and says it changed meanwhile", async () => {
    const { spy } = setup();
    vi.spyOn(dashboardsApi, "applyTemplateUpdate").mockRejectedValue(new DashboardConflictError("Konflikt.", 6));
    fireEvent.click(screen.getByRole("button", { name: "Übernehmen" }));
    expect(await screen.findByText("Das Dashboard wurde inzwischen geändert.")).toBeVisible();
    expect(spy).toHaveBeenCalledWith({ queryKey: dashboardKeys.one(7) });
  });

  it("a 422 shows the server's message (more than 40 widgets)", async () => {
    setup();
    const message = "Das Dashboard hätte dann mehr als 40 Widgets.";
    vi.spyOn(dashboardsApi, "applyTemplateUpdate").mockRejectedValue(
      new ApiError(422, { detail: { message, path: "widgets" } }, "/api/v1/dashboards/7/template-update"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Übernehmen" }));
    expect(await screen.findByText(message)).toBeVisible();
    expect(screen.getByRole("button", { name: "Übernehmen" })).toBeEnabled();
  });

  it("any other failure shows a generic message", async () => {
    setup();
    vi.spyOn(dashboardsApi, "applyTemplateUpdate").mockRejectedValue(new Error("boom"));
    fireEvent.click(screen.getByRole("button", { name: "Ausblenden" }));
    expect(await screen.findByText("Das hat nicht geklappt. Bitte versuchen Sie es noch einmal.")).toBeVisible();
  });
});
