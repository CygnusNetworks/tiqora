import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { ApiError, type TicketListItem } from "@/lib/api";
import { DashboardConflictError, dashboardsApi, type DrillPage as DrillPageData } from "@/lib/dashboardsApi";
import { demoDashboard } from "@/demo/dashboardsMock";
import { SavedSearchEditorPage } from "@/routes/agent/SavedSearchEditorPage";
import { DrillPage } from "./DrillPage";
import { validateDrillSearch } from "./drillLinks";

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 1, is_admin: false, can_share_reports: false } }),
  useOptionalAuth: () => ({ user: { id: 1 } }),
}));

const TICKET = {
  id: 101,
  tn: "2026100101",
  title: "Drucker streikt",
  queue_id: 1,
  queue_name: "Support",
  state_id: 4,
  state: "open",
  state_type: "open",
  priority_id: 3,
  priority: "3 normal",
  lock_id: 1,
  lock: "unlock",
  owner_id: 5,
  create_time: "2026-10-01T12:00:00",
  change_time: "2026-10-01T12:00:00",
  escalation_time: 0,
  escalation_response_time: 0,
  escalation_update_time: 0,
  escalation_solution_time: 0,
  until_time: 0,
  attachment_count: 0,
  channel: "email",
  has_ai_summary: false,
  ai_escalated: false,
  ai_paused: false,
  archive_flag: 0,
} as TicketListItem;
const FILTER = { version: 1, where: { field: "queue_id", op: "eq", value: 1 } };

function page(over: Partial<DrillPageData> = {}): DrillPageData {
  return { title: "Meine offenen Tickets", total: 120, items: [TICKET], filter: FILTER, note: null, ...over };
}

const ticketList = (id: string, y: number) => ({
  id,
  type_key: "core.ticket-list",
  type_version: 1,
  title_override: null,
  settings: {},
  pinned: false,
  layout: { lg: { x: 0, y, w: 12, h: 5 } },
});

function renderAt(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> });
  const agent = createRoute({ getParentRoute: () => root, path: "/agent", component: () => <p data-testid="dashboard">Start</p> });
  const drill = createRoute({ getParentRoute: () => root, path: "/agent/tickets/drill", validateSearch: validateDrillSearch, component: DrillPage });
  const created = createRoute({ getParentRoute: () => root, path: "/agent/searches/new", component: SavedSearchEditorPage });
  const ticket = createRoute({ getParentRoute: () => root, path: "/agent/tickets/$ticketId", component: () => null });
  const router = createRouter({
    routeTree: root.addChildren([agent, drill, created, ticket]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <RouterProvider router={router} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

beforeAll(async () => {
  window.scrollTo = vi.fn();
  await i18n.changeLanguage("de");
});
beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(dashboardsApi, "get").mockResolvedValue(demoDashboard({ id: 7, can_edit: true, version: 3, widgets: [ticketList("w-one", 0)] }));
});

describe("DrillPage", () => {
  it("lists the tickets with paging, shows the note and the way back", async () => {
    const drill = vi.spyOn(dashboardsApi, "drillTickets").mockResolvedValue(page({ note: "Der Bericht kann einige Minuten älter sein als diese Liste." }));
    renderAt("/agent/tickets/drill?d=7&w=w-one&offset=50&sort=age&order=asc");
    expect(await screen.findByText("Drucker streikt")).toBeVisible();
    expect(screen.getByTestId("drill-title")).toHaveTextContent("Tickets zu „Meine offenen Tickets“");
    expect(screen.getByTestId("drill-total")).toHaveTextContent("120");
    expect(screen.getByTestId("drill-note")).toHaveTextContent("einige Minuten älter");
    expect(screen.getByRole("link", { name: "Zurück zum Dashboard" })).toHaveAttribute("href", "/agent?d=7");
    expect(drill).toHaveBeenCalledWith(7, "w-one", { offset: 50, limit: 50, sort: "age", order: "asc", point: undefined }, expect.anything());
  });

  it("sends a chart point as JSON, never a filter", async () => {
    const drill = vi.spyOn(dashboardsApi, "drillTickets").mockResolvedValue(page());
    renderAt(`/agent/tickets/drill?d=7&w=w-one&point=${encodeURIComponent('{"queue":3}')}`);
    await screen.findByText("Drucker streikt");
    expect(drill.mock.calls[0][2]).toMatchObject({ point: '{"queue":3}' });
  });

  it("'Als Suche speichern' navigates with the filter in router state, never in the URL", async () => {
    vi.spyOn(dashboardsApi, "drillTickets").mockResolvedValue(page());
    const router = renderAt("/agent/tickets/drill?d=7&w=w-one");
    fireEvent.click(await screen.findByTestId("drill-save-search"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/searches/new"));
    expect(router.state.location.search).toEqual({});
    expect(router.state.location.href).not.toContain("queue_id");
    expect(router.state.location.state).toMatchObject({ criteria: FILTER, name: "Meine offenen Tickets" });
    // the editor starts from it
    expect(await screen.findByTestId("saved-search-editor")).toBeVisible();
    expect(screen.getByDisplayValue("Meine offenen Tickets")).toBeVisible();
  });

  it("'Als Widget hinzufügen' appends a ticket-list widget at the first free spot", async () => {
    vi.spyOn(dashboardsApi, "drillTickets").mockResolvedValue(page());
    const save = vi.spyOn(dashboardsApi, "save").mockResolvedValue(demoDashboard({ id: 7 }));
    renderAt("/agent/tickets/drill?d=7&w=w-one");
    await waitFor(() => expect(screen.getByTestId("drill-add-widget")).toBeEnabled());
    fireEvent.click(screen.getByTestId("drill-add-widget"));
    expect(await screen.findByTestId("drill-added")).toBeVisible();
    const body = save.mock.calls[0][1];
    expect(save.mock.calls[0][0]).toBe(7);
    expect(body.version).toBe(3);
    expect(body.widgets).toHaveLength(2);
    const added = body.widgets[1];
    expect(added.type_key).toBe("core.ticket-list");
    expect(added.settings).toMatchObject({ source: { kind: "filter", filter: FILTER } });
    // the first widget fills the grid's first rows (y 0..5): the new one goes below it
    expect(added.layout.lg.y).toBe(5);
    expect(body.widgets[0].id).toBe("w-one");
  });

  it("a stale dashboard version shows the conflict message", async () => {
    vi.spyOn(dashboardsApi, "drillTickets").mockResolvedValue(page());
    vi.spyOn(dashboardsApi, "save").mockRejectedValue(new DashboardConflictError("x", 4));
    renderAt("/agent/tickets/drill?d=7&w=w-one");
    await waitFor(() => expect(screen.getByTestId("drill-add-widget")).toBeEnabled());
    fireEvent.click(screen.getByTestId("drill-add-widget"));
    expect(await screen.findByTestId("drill-add-error")).toHaveTextContent("Das Dashboard wurde inzwischen geändert. Bitte erneut versuchen.");
    expect(screen.getByTestId("drill-add-widget")).toBeEnabled();
  });

  it("hides both buttons when the filter is null, and the widget button when the viewer cannot edit", async () => {
    vi.spyOn(dashboardsApi, "drillTickets").mockResolvedValue(page({ filter: null }));
    renderAt("/agent/tickets/drill?d=7&w=w-one");
    await screen.findByText("Drucker streikt");
    expect(screen.queryByTestId("drill-save-search")).toBeNull();
    expect(screen.queryByTestId("drill-add-widget")).toBeNull();
  });

  it("a viewer who cannot edit the dashboard can still save the search", async () => {
    vi.spyOn(dashboardsApi, "get").mockResolvedValue(demoDashboard({ id: 7, can_edit: false }));
    vi.spyOn(dashboardsApi, "drillTickets").mockResolvedValue(page());
    renderAt("/agent/tickets/drill?d=7&w=w-one");
    await screen.findByText("Drucker streikt");
    expect(screen.getByTestId("drill-save-search")).toBeVisible();
    expect(screen.queryByTestId("drill-add-widget")).toBeNull();
  });

  it("shows the server's message when there is no list for the point", async () => {
    vi.spyOn(dashboardsApi, "drillTickets").mockRejectedValue(
      new ApiError(422, { detail: { message: "Für diesen Wert gibt es keine Ticketliste." } }, "/api/v1/dashboards/7/widgets/w-one/tickets"),
    );
    renderAt("/agent/tickets/drill?d=7&w=w-one");
    expect(await screen.findByRole("alert")).toHaveTextContent("Für diesen Wert gibt es keine Ticketliste.");
  });

  it("an address without ids shows the invalid notice and calls nothing", async () => {
    const drill = vi.spyOn(dashboardsApi, "drillTickets");
    renderAt("/agent/tickets/drill");
    expect(await screen.findByTestId("drill-invalid")).toBeVisible();
    expect(drill).not.toHaveBeenCalled();
  });
});
