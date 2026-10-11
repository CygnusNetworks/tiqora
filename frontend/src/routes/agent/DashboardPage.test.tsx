import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import i18n from "@/i18n";
import { api, ApiError } from "@/lib/api";
import {
  DashboardConflictError,
  dashboardsApi,
  type Dashboard,
  type DashboardTab,
  type DashboardWidgetRow,
  type WidgetResult,
} from "@/lib/dashboardsApi";
import { handleSSEMessage } from "@/lib/useSSE";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { DASHBOARD_WIDGETS, defineWidget, type WidgetProps } from "@/plugins/dashboardWidgets";
import { demoDashboard, demoDefaultWidgets, demoTab, demoWidgetData } from "@/demo/dashboardsMock";
import { registerCoreWidgets } from "@/dashboard/widgets";
import { DashboardPage } from "./DashboardPage";

// ---------------------------------------------------------------- mocks

const auth = vi.hoisted(() => ({
  user: { id: 5, login: "anna", first_name: "Anna", is_admin: false, can_view_reports: false },
}));
vi.mock("@/auth/AuthContext", async () => {
  const actual = await vi.importActual<typeof import("@/auth/AuthContext")>("@/auth/AuthContext");
  return { ...actual, useAuth: () => auth, useOptionalAuth: () => auth };
});
vi.mock("@/plugins/PluginProvider", async () => {
  const actual = await vi.importActual<typeof import("@/plugins/PluginProvider")>("@/plugins/PluginProvider");
  return { ...actual, usePlugins: () => ({ plugins: [], ready: true, safeMode: false }) };
});

/** ResizeObserver stand-in: reports the width set by `setContainerWidth` (default: a wide desktop). */
let containerWidth = 1200;
const observers = new Set<{ cb: ResizeObserverCallback; self: ResizeObserver }>();
class FakeResizeObserver {
  private entry: { cb: ResizeObserverCallback; self: ResizeObserver };
  constructor(cb: ResizeObserverCallback) {
    this.entry = { cb, self: this as unknown as ResizeObserver };
  }
  observe() {
    observers.add(this.entry);
    this.entry.cb([{ contentRect: { width: containerWidth } } as ResizeObserverEntry], this.entry.self);
  }
  unobserve() {}
  disconnect() {
    observers.delete(this.entry);
  }
}
function setContainerWidth(width: number) {
  containerWidth = width;
  act(() => {
    for (const o of observers) o.cb([{ contentRect: { width } } as ResizeObserverEntry], o.self);
  });
}

// ---------------------------------------------------------------- fixtures

/** Readable ids for the eleven default widgets (same order, types and layout as the default). */
const IDS = [
  "w-counter-1",
  "w-counter",
  "w-counter-3",
  "w-counter-4",
  "w-esc",
  "w-list-new",
  "w-list",
  "w-list-inbox",
  "w-reminders",
  "w-recent",
  "w-queues",
];
const DEFAULT_WIDGETS: DashboardWidgetRow[] = demoDefaultWidgets().map((w, i) => ({ ...w, id: IDS[i] }));
const DEFAULT_TYPES_IN_ORDER = DEFAULT_WIDGETS.map((w) => w.type_key);
const DEFAULT_DASHBOARD = demoDashboard({ id: 1, name: "Mein Start", widgets: DEFAULT_WIDGETS });

function wire(id: string, type_key: string, layout: DashboardWidgetRow["layout"], settings = {}): DashboardWidgetRow {
  return { id, type_key, type_version: 1, title_override: null, settings, layout };
}

const SHARED = demoDashboard({
  id: 2,
  name: "Team Support",
  kind: "shared",
  is_owner: false,
  can_edit: false,
  can_share: false,
  owner_name: "Maya Rivera",
  widgets: [wire("t-team", "core.team-presence", { lg: { x: 0, y: 0, w: 4, h: 3 } })],
});
const LOCKED = demoDashboard({
  id: 3,
  name: "Leitstand",
  kind: "template",
  locked: true,
  is_owner: false,
  can_edit: false,
  can_share: false,
  widgets: [wire("l-notes", "core.notes", { lg: { x: 0, y: 0, w: 4, h: 2 } }, { markdown: "Hallo" })],
});

const tabOf = ({ widgets: _w, ...tab }: Dashboard): DashboardTab => demoTab(tab);

let dashboards: Map<number, Dashboard>;
let tabs: DashboardTab[];

/** Replace dashboard 1 (and its tab) — the one shown by default. */
function mockDashboard(over: Partial<Dashboard>) {
  const d = demoDashboard({ ...DEFAULT_DASHBOARD, ...over, id: over.id ?? 1 });
  dashboards.set(d.id, d);
  tabs = tabs.map((tab) => (tab.id === d.id ? tabOf(d) : tab));
}

const notFound = () => new ApiError(404, { detail: "Nicht gefunden." }, "/api/v1/dashboards/99");
const ok = (): WidgetResult => ({ status: "ok", data: { value: 1, delta: null, spark: [] } });

const apiMock = {
  list: vi.fn(),
  ensureStart: vi.fn(),
  get: vi.fn(),
  data: vi.fn(),
  widgetData: vi.fn(),
};

// ---------------------------------------------------------------- render

let client: QueryClient;
let currentRouter: { state: { location: { search: Record<string, unknown> } } } | null = null;

async function renderPage(search: { d?: number } = {}) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute();
  const home = createRoute({ getParentRoute: () => rootRoute, path: "/agent", component: DashboardPage });
  const targets = ["/agent/tickets/$ticketId", "/agent/queues", "/agent/calendar"].map((path) =>
    createRoute({ getParentRoute: () => rootRoute, path }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren([home, ...targets]),
    history: createMemoryHistory({ initialEntries: [search.d ? `/agent?d=${search.d}` : "/agent"] }),
  });
  await act(async () => {
    await router.load();
  });
  currentRouter = router;
  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

/** Lets fake timers and resolved promises run. */
async function flush(ms = 0) {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(i === 0 ? ms : 0);
    });
  }
}

const emitSSE = (message: object) => handleSSEMessage(client, JSON.stringify(message));

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { value: hidden, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  containerWidth = 1200;
  observers.clear();
  setDisplayTimeZone("Europe/Berlin");
  localStorage.clear();
  auth.user = { id: 5, login: "anna", first_name: "Anna", is_admin: false, can_view_reports: false };
  registerCoreWidgets();
  window.scrollTo = vi.fn() as never;
  dashboards = new Map([DEFAULT_DASHBOARD, SHARED, LOCKED].map((d) => [d.id, d]));
  tabs = [DEFAULT_DASHBOARD, SHARED, LOCKED].map(tabOf);

  apiMock.list = vi.spyOn(dashboardsApi, "list").mockImplementation(async () => tabs) as never;
  apiMock.ensureStart = vi.spyOn(dashboardsApi, "ensureStart").mockResolvedValue(DEFAULT_DASHBOARD) as never;
  apiMock.get = vi.spyOn(dashboardsApi, "get").mockImplementation(async (id: number) => {
    const d = dashboards.get(id);
    if (!d) throw notFound();
    return d;
  }) as never;
  // like the server: every requested widget of that dashboard that has data
  apiMock.data = vi.spyOn(dashboardsApi, "data").mockImplementation(async (id: number, ids?: string[]) => {
    const out: Record<string, WidgetResult> = {};
    for (const w of dashboards.get(id)?.widgets ?? []) {
      if (ids && !ids.includes(w.id)) continue;
      const r = demoWidgetData(w.type_key, w.settings ?? {});
      if (r) out[w.id] = r;
    }
    return { widgets: out };
  }) as never;
  apiMock.widgetData = vi.spyOn(dashboardsApi, "widgetData").mockResolvedValue(ok()) as never;
  vi.spyOn(api, "listQueues").mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Object.defineProperty(document, "hidden", { value: false, configurable: true });
  for (const key of DASHBOARD_WIDGETS.list().map((w) => w.key)) {
    if (!key.startsWith("core.")) {
      // plugin defs registered by a test: rebuild the point with the core widgets only
      DASHBOARD_WIDGETS.clearForTests();
      registerCoreWidgets();
      break;
    }
  }
});

// ---------------------------------------------------------------- first visit and header

describe("DashboardPage", () => {
  it("first visit: no tab yet triggers ensure-start and renders the default dashboard", async () => {
    apiMock.list.mockResolvedValueOnce([]);
    await renderPage();
    expect(await screen.findByRole("tab", { name: "Mein Start" })).toHaveAttribute("aria-selected", "true");
    expect(apiMock.ensureStart).toHaveBeenCalledTimes(1);
    expect((await screen.findByTestId("dashboard-grid")).children).toHaveLength(11);
  });

  it("first visit with only a locked role template still creates 'Mein Start' and keeps the template tab", async () => {
    tabs = [tabOf(LOCKED)];
    apiMock.list.mockResolvedValueOnce([tabOf(LOCKED)]).mockImplementation(async () => [tabOf(DEFAULT_DASHBOARD), tabOf(LOCKED)]);
    await renderPage();
    expect(await screen.findByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Leitstand" })).toBeVisible();
    expect(apiMock.ensureStart).toHaveBeenCalledTimes(1);
  });

  it("a failed first-visit setup shows an error whose retry tries again", async () => {
    apiMock.list.mockResolvedValueOnce([]);
    apiMock.ensureStart.mockRejectedValueOnce(new Error("boom"));
    await renderPage();
    expect(await screen.findByText("Das Dashboard konnte nicht geladen werden.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(apiMock.ensureStart).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
  });

  it("an agent who owns a dashboard never triggers ensure-start", async () => {
    await renderPage();
    await screen.findByTestId("dashboard-grid");
    expect(apiMock.ensureStart).not.toHaveBeenCalled();
  });

  it("greeting and date stay above the grid", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-10T07:00:00Z")); // 09:00 in Europe/Berlin -> morning
    await renderPage();
    const h1 = await screen.findByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent("Guten Morgen, Anna");
    expect(screen.getByText(/Samstag, 10. Oktober 2026/)).toBeVisible();
    const grid = await screen.findByTestId("dashboard-grid");
    expect(h1.compareDocumentPosition(grid) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("tabs: roving arrow-key focus, aria-selected, '+' label, lock hint on a locked template, owner on a shared tab", async () => {
    await renderPage();
    const mine = await screen.findByRole("tab", { name: "Mein Start" });
    expect(mine).toHaveAttribute("aria-selected", "true");
    expect(mine).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Team Support" })).toHaveAttribute("tabindex", "-1");
    fireEvent.keyDown(mine, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Team Support" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Team Support" }), { key: "End" });
    expect(screen.getByRole("tab", { name: "Leitstand" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Leitstand" }), { key: "ArrowRight" });
    expect(mine).toHaveFocus();
    // focus moves, the selection does not (manual activation)
    expect(mine).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Neues Dashboard" })).toBeVisible();
    expect(screen.getByRole("tablist")).not.toContainElement(screen.getByRole("button", { name: "Neues Dashboard" }));
    expect(screen.getByRole("tab", { name: "Leitstand" })).toHaveAccessibleDescription("Verbindlich");
    expect(screen.getByRole("tab", { name: "Team Support" })).toHaveAccessibleDescription("von Maya Rivera");
    expect(within(screen.getByRole("tab", { name: "Team Support" })).getByText("· Maya Rivera")).toBeVisible();
    expect(mine).not.toHaveAccessibleDescription();
  });

  it("selecting a tab loads that dashboard and remembers it in localStorage; a broken storage is ignored", async () => {
    await renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Team Support" }));
    expect(apiMock.get).toHaveBeenLastCalledWith(2, expect.anything());
    expect(await screen.findByTestId("widget-t-team")).toBeVisible();
    expect(localStorage.getItem("tiqora.dashboard.last")).toBe("2");
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    fireEvent.click(screen.getByRole("tab", { name: "Mein Start" }));
    expect(screen.getByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
    expect(await screen.findByTestId("widget-w-counter-1")).toBeVisible();
  });

  it("picking a tab drops ?d= from the address, so a reload opens the picked dashboard", async () => {
    await renderPage({ d: 2 });
    expect(await screen.findByRole("tab", { name: "Team Support", selected: true })).toBeVisible();
    expect(currentRouter!.state.location.search).toEqual({ d: 2 });
    fireEvent.click(screen.getByRole("tab", { name: "Mein Start" }));
    await waitFor(() => expect(currentRouter!.state.location.search).toEqual({}));
    expect(screen.getByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
    expect(localStorage.getItem("tiqora.dashboard.last")).toBe("1");
    cleanup();
    await renderPage({}); // the "reload"
    expect(await screen.findByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
  });

  it("the content area is the tab panel of the selected tab; loading is announced", async () => {
    let release: (d: Dashboard) => void = () => {};
    apiMock.get.mockImplementationOnce(() => new Promise<Dashboard>((r) => (release = r)));
    await renderPage();
    const loading = await screen.findByRole("status", { name: "Dashboard wird geladen" });
    expect(loading).toHaveAttribute("aria-busy", "true");
    await act(async () => release(DEFAULT_DASHBOARD));
    const panel = screen.getByRole("tabpanel", { name: "Mein Start" });
    expect(panel).toContainElement(await screen.findByTestId("dashboard-grid"));
    expect(screen.getByRole("tab", { name: "Mein Start" })).toHaveAttribute("aria-controls", panel.id);
  });

  it("the placeholder buttons look disabled (no hover look, dimmed, not-allowed cursor); 'Anpassen' is live", async () => {
    await renderPage();
    await screen.findByTestId("dashboard-grid");
    for (const name of ["Teilen", "Neues Dashboard"]) {
      const button = screen.getByRole("button", { name });
      expect(button).toBeVisible();
      expect(button).toBeDisabled();
      expect(button).toHaveClass("disabled:opacity-50", "disabled:cursor-not-allowed");
    }
    expect(screen.getByRole("button", { name: "Anpassen" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Teilen" })).toHaveClass("disabled:hover:bg-surface");
    expect(screen.getByRole("button", { name: "Neues Dashboard" })).toHaveClass("disabled:hover:text-muted");
  });

  it("the remembered dashboard opens on the next visit", async () => {
    localStorage.setItem("tiqora.dashboard.last", "2");
    await renderPage();
    expect(await screen.findByRole("tab", { name: "Team Support", selected: true })).toBeVisible();
  });

  it("?d=<id> selects that dashboard; an id the agent may not see falls back to the first tab", async () => {
    await renderPage({ d: 2 });
    expect(await screen.findByRole("tab", { name: "Team Support", selected: true })).toBeVisible();
    cleanup();
    await renderPage({ d: 99 });
    expect(await screen.findByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
    expect(apiMock.get).not.toHaveBeenCalledWith(99, expect.anything());
  });

  it("an admin's ?d= that does not exist falls back to the first tab after the 404", async () => {
    auth.user = { ...auth.user, is_admin: true };
    await renderPage({ d: 99 });
    await waitFor(() => expect(apiMock.get).toHaveBeenCalledWith(99, expect.anything()));
    expect(await screen.findByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
    expect(await screen.findByTestId("dashboard-grid")).toBeVisible();
    expect(screen.queryByText("Das Dashboard konnte nicht geladen werden.")).toBeNull();
  });

  it("view mode is clean: no handles, no toolbar; 'Anpassen' only when can_edit; 'Teilen' only when can_share", async () => {
    await renderPage();
    await screen.findByTestId("dashboard-grid");
    expect(screen.queryByTestId(/widget-.*-handle/)).toBeNull();
    expect(screen.queryByRole("toolbar")).toBeNull();
    expect(screen.getByRole("button", { name: "Anpassen" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Teilen" })).toBeVisible();
    cleanup();
    mockDashboard({ can_edit: false, can_share: false, kind: "shared", is_owner: false });
    await renderPage();
    await screen.findByTestId("dashboard-grid");
    expect(screen.queryByRole("button", { name: "Anpassen" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Teilen" })).toBeNull();
  });

  // ---------------------------------------------------------------- grid

  it("widgets are placed from layout.lg on a wide container: grid-column/row styles", async () => {
    await renderPage();
    const grid = await screen.findByTestId("dashboard-grid");
    expect(grid).toHaveAttribute("data-breakpoint", "lg");
    expect(grid.style.gridTemplateColumns).toBe("repeat(12, minmax(0, 1fr))");
    expect(grid.style.gridAutoRows).toBe("64px");
    const el = await screen.findByTestId("widget-w-counter-1");
    expect(el.style.gridColumn).toBe("1 / span 3");
    expect(el.style.gridRow).toBe("1 / span 2");
    expect(screen.getByTestId("widget-w-list").style.gridColumn).toBe("7 / span 6");
    expect(screen.getByTestId("widget-w-list").style.gridRow).toBe("6 / span 5");
  });

  it("md container derives the 8-column layout; sm renders one column in array order with counters in a 2-column group", async () => {
    await renderPage();
    await screen.findByTestId("widget-w-counter-1");
    setContainerWidth(700);
    const md = await screen.findByTestId("dashboard-grid");
    expect(md).toHaveAttribute("data-breakpoint", "md");
    expect(md.style.gridTemplateColumns).toBe("repeat(8, minmax(0, 1fr))");
    expect(screen.getByTestId("widget-w-counter-1").style.gridColumn).toBe("1 / span 2");
    setContainerWidth(390);
    const grid = screen.getByTestId("dashboard-grid");
    expect(grid).toHaveAttribute("data-breakpoint", "sm");
    expect(Array.from(grid.querySelectorAll("[data-type]")).map((e) => e.getAttribute("data-type"))).toEqual(
      DEFAULT_TYPES_IN_ORDER,
    );
    const group = screen.getByTestId("counter-group");
    expect(group).toHaveClass("grid-cols-2");
    expect(group.querySelectorAll("[data-type='core.counter']")).toHaveLength(4);
    expect(screen.getByTestId("widget-w-counter-1").style.gridColumn).toBe("");
  });

  it("exactly one batch request per dashboard view, none per widget", async () => {
    await renderPage();
    await screen.findByTestId("widget-w-counter-1");
    expect(within(screen.getByTestId("widget-w-counter-1")).getByText("12")).toBeVisible();
    expect(apiMock.data).toHaveBeenCalledTimes(1);
    expect(apiMock.data).toHaveBeenCalledWith(1, undefined, expect.anything());
    expect(apiMock.widgetData).not.toHaveBeenCalled();
  });

  it("a pending widget is fetched on its own and the rest stay rendered", async () => {
    mockDashboard({
      widgets: [
        wire("a", "core.counter", { lg: { x: 0, y: 0, w: 3, h: 2 } }),
        wire("b", "core.counter", { lg: { x: 3, y: 0, w: 3, h: 2 } }),
      ],
    });
    apiMock.data.mockResolvedValue({ widgets: { a: ok(), b: { status: "pending" } } });
    await renderPage();
    expect(await screen.findByTestId("widget-a")).toBeVisible();
    expect(screen.getByTestId("widget-b-skeleton")).toBeVisible();
    expect(within(screen.getByTestId("widget-b-skeleton")).getByTestId("widget-skeleton")).toBeVisible();
    await waitFor(() => expect(apiMock.widgetData).toHaveBeenCalledWith(1, "b", expect.anything()), { timeout: 4000 });
    expect(await screen.findByTestId("widget-b")).toBeVisible();
    expect(screen.getByTestId("widget-a")).toBeVisible();
    expect(apiMock.data).toHaveBeenCalledTimes(1);
  });

  it("slow provider does not block the page: error tile for one widget, 'Erneut versuchen' refetches only that one", async () => {
    mockDashboard({
      widgets: [
        wire("a", "core.counter", { lg: { x: 0, y: 0, w: 3, h: 2 } }),
        wire("b", "core.counter", { lg: { x: 3, y: 0, w: 3, h: 2 } }),
      ],
    });
    apiMock.data.mockResolvedValue({
      widgets: { a: ok(), b: { status: "error", error: "Diese Daten konnten nicht geladen werden." } },
    });
    await renderPage();
    const b = await screen.findByTestId("widget-b");
    expect(within(b).getByText("Diese Daten konnten nicht geladen werden.")).toBeVisible();
    expect(within(screen.getByTestId("widget-a")).queryByRole("button", { name: "Erneut versuchen" })).toBeNull();
    fireEvent.click(within(b).getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(apiMock.widgetData).toHaveBeenCalledTimes(1));
    expect(apiMock.widgetData).toHaveBeenCalledWith(1, "b", expect.anything());
    expect(apiMock.data).toHaveBeenCalledTimes(1);
    expect(await screen.findByTestId("widget-b")).not.toHaveTextContent("Diese Daten konnten nicht geladen werden.");
  });

  it("SSE ticket_changed refreshes only widgets listing it in refreshOn, coalesced to one request", async () => {
    vi.useFakeTimers();
    await renderPage();
    await flush();
    expect(screen.getByTestId("widget-w-list")).toBeVisible();
    expect(apiMock.data).toHaveBeenCalledTimes(1);
    emitSSE({ type: "ticket_changed", ticket_id: 1, event: "x" });
    emitSSE({ type: "ticket_changed", ticket_id: 2, event: "x" });
    await flush(2100);
    expect(apiMock.data).toHaveBeenCalledTimes(2);
    expect(apiMock.data).toHaveBeenLastCalledWith(1, expect.arrayContaining(["w-list", "w-counter", "w-esc"]), expect.anything());
    expect(apiMock.data.mock.calls.at(-1)![1]).not.toContain("w-recent");
    expect(apiMock.data.mock.calls.at(-1)![1]).not.toContain("w-queues");
  });

  it("interval:60 widgets refetch on their timer and pause while the tab is hidden", async () => {
    vi.useFakeTimers();
    await renderPage();
    await flush();
    const calls = apiMock.data.mock.calls.length;
    await flush(60_000);
    expect(apiMock.data.mock.calls.length).toBe(calls + 1);
    expect(apiMock.data.mock.calls.at(-1)![1]).toEqual(["w-esc"]); // only the escalations widget lists interval:60
    setHidden(true);
    await flush(120_000);
    expect(apiMock.data.mock.calls.length).toBe(calls + 1);
  });

  it("a widget type that is not registered renders the unavailable tile in its slot", async () => {
    mockDashboard({ widgets: [wire("g1", "org.example.gone", { lg: { x: 3, y: 1, w: 4, h: 2 } })] });
    await renderPage();
    const tile = await screen.findByTestId("widget-g1");
    expect(within(tile).getByText("Widget nicht verfügbar")).toBeVisible();
    expect(within(tile).queryByRole("button", { name: "Entfernen" })).toBeNull();
    expect(tile.style.gridColumn).toBe("4 / span 4");
    expect(tile.style.gridRow).toBe("2 / span 2");
  });

  it("a stored layout wider than 12 columns is clamped, not overflowing the grid", async () => {
    mockDashboard({ widgets: [wire("g2", "core.links", { lg: { x: 10, y: 0, w: 6, h: 2 } })] });
    await renderPage();
    expect((await screen.findByTestId("widget-g2")).style.gridColumn).toBe("7 / span 6");
  });

  it("plugin widgets registered in DASHBOARD_WIDGETS render in the grid inside a host frame; core widgets keep their single frame", async () => {
    function Weather(_props: WidgetProps) {
      return <p>Wetter Bonn</p>;
    }
    DASHBOARD_WIDGETS.register(
      defineWidget({
        key: "org.example.weather",
        owner: "org.example",
        version: 1,
        titleKey: "widget.title",
        category: "other",
        icon: "sun",
        size: { default: [4, 2], min: [2, 2], max: [6, 4] },
        settingsSchema: null,
        dataEndpoint: null,
        refreshOn: [],
        permission: null,
        component: Weather,
      }),
      "org.example",
    );
    mockDashboard({
      widgets: [
        wire("p1", "org.example.weather", { lg: { x: 0, y: 0, w: 4, h: 2 } }),
        wire("c1", "core.counter", { lg: { x: 4, y: 0, w: 3, h: 2 } }),
      ],
    });
    await renderPage();
    expect(await screen.findByText("Wetter Bonn")).toBeVisible();
    const cell = screen.getByTestId("widget-p1");
    expect(within(cell).getByTestId("plugin-widget-org.example.weather")).toContainElement(screen.getByText("Wetter Bonn"));
    expect(cell.style.gridColumn).toBe("1 / span 4");
    const counter = await screen.findByTestId("widget-c1");
    expect(counter.querySelectorAll("article")).toHaveLength(1);
    expect(apiMock.data).toHaveBeenCalledTimes(1);
  });

  it("a widget that throws while rendering shows an error tile; the others stay", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    function Broken(): never {
      throw new Error("kaputt");
    }
    DASHBOARD_WIDGETS.register(
      defineWidget({
        key: "org.example.broken",
        owner: "org.example",
        version: 1,
        titleKey: "widget.title",
        category: "other",
        icon: "x",
        size: { default: [4, 2], min: [2, 2], max: [6, 4] },
        settingsSchema: null,
        dataEndpoint: null,
        refreshOn: [],
        permission: null,
        component: Broken,
      }),
      "org.example",
    );
    mockDashboard({
      widgets: [
        wire("x1", "org.example.broken", { lg: { x: 0, y: 0, w: 4, h: 2 } }),
        wire("c1", "core.counter", { lg: { x: 4, y: 0, w: 3, h: 2 } }),
      ],
    });
    await renderPage();
    expect(await within(await screen.findByTestId("widget-x1")).findByText("Dieses Widget konnte nicht angezeigt werden.")).toBeVisible();
    expect(await screen.findByTestId("widget-c1")).toBeVisible();
  });

  it("a widget the viewer may not use renders the forbidden tile", async () => {
    DASHBOARD_WIDGETS.register(
      defineWidget({
        key: "org.example.stats",
        owner: "org.example",
        version: 1,
        titleKey: "widget.title",
        category: "other",
        icon: "chart",
        size: { default: [4, 2], min: [2, 2], max: [6, 4] },
        settingsSchema: null,
        dataEndpoint: null,
        refreshOn: [],
        permission: "stats",
        component: () => <p>Geheime Zahlen</p>,
      }),
      "org.example",
    );
    mockDashboard({ widgets: [wire("s1", "org.example.stats", { lg: { x: 0, y: 0, w: 4, h: 2 } })] });
    await renderPage();
    const tile = await screen.findByTestId("widget-s1");
    expect(within(tile).getByText("Dafür fehlt dir die Berechtigung.")).toBeVisible();
    expect(screen.queryByText("Geheime Zahlen")).toBeNull();
  });

  // ---------------------------------------------------------------- templates and errors

  it("an admin opening a template by ?d= sees the template banner; a viewer of a locked one sees the lock hint and no Anpassen", async () => {
    auth.user = { ...auth.user, is_admin: true };
    dashboards.set(9, demoDashboard({ id: 9, name: "Support-Team", kind: "template", can_edit: true, can_share: false, is_owner: false, locked: false, widgets: [] }));
    await renderPage({ d: 9 });
    expect(await screen.findByText(/Vorlage/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Anpassen" })).toBeVisible();
    expect(screen.queryAllByRole("tab", { selected: true })).toHaveLength(0);
    cleanup();
    auth.user = { ...auth.user, is_admin: false };
    localStorage.setItem("tiqora.dashboard.last", "3");
    await renderPage({});
    expect(await screen.findByText(/verbindlich/)).toBeVisible();
    expect(screen.getByRole("tab", { name: "Leitstand", selected: true })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Anpassen" })).toBeNull();
  });

  it("a dashboard that fails to load shows an error with retry while the tabs stay usable", async () => {
    apiMock.get.mockRejectedValueOnce(new Error("boom"));
    await renderPage();
    expect(await screen.findByText("Das Dashboard konnte nicht geladen werden.")).toBeVisible();
    expect(screen.getAllByRole("tab").length).toBe(3);
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByTestId("dashboard-grid")).toBeVisible();
    expect(screen.getAllByRole("tab").length).toBeGreaterThan(0);
  });

  it("the old plugin 'Erweiterungen' section is gone", async () => {
    await renderPage();
    await screen.findByTestId("dashboard-grid");
    expect(screen.queryByTestId("plugin-widgets")).toBeNull();
    expect(screen.queryByText("Erweiterungen")).toBeNull();
  });

  // ---------------------------------------------------------------- edit mode (Task 12)

  describe("edit mode", () => {
    const KEY = "tiqora.dashboard.draft.5.1";
    // grid 1200 px wide: one column step (1200 - 11 * 16) / 12 + 16 = 101.33 px, one row step 80 px
    const STEP_X = (1200 - 11 * 16) / 12 + 16;
    const STEP_Y = 80;
    let save: ReturnType<typeof vi.fn>;
    let previewData: ReturnType<typeof vi.fn>;

    /** jsdom has no PointerEvent: a MouseEvent of the pointer type carries clientX/Y. */
    function firePointer(target: EventTarget, type: string, init: { clientX?: number; clientY?: number } = {}) {
      const ev = new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientX: init.clientX ?? 0, clientY: init.clientY ?? 0 });
      Object.defineProperty(ev, "pointerId", { value: 1 });
      act(() => {
        target.dispatchEvent(ev);
      });
    }
    function mockGridRect(width = 1200) {
      vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
        left: 0, top: 0, right: width, bottom: 2000, width, height: 2000, x: 0, y: 0, toJSON: () => ({}),
      } as DOMRect);
    }
    async function enterEdit() {
      await renderPage();
      fireEvent.click(await screen.findByRole("button", { name: "Anpassen" }));
      return screen.findByRole("toolbar", { name: "Bearbeiten" });
    }
    const cellOf = (id: string) => screen.getByTestId(`widget-${id}`);
    const rowStart = (id: string) => Number(cellOf(id).style.gridRow.split(" / ")[0]);
    const order = () =>
      Array.from(screen.getByTestId("dashboard-grid").querySelectorAll<HTMLElement>("[data-type]")).map((el) => el.dataset.widgetId);
    const undoButton = () => screen.getByRole("button", { name: "Rückgängig" });
    const redoButton = () => screen.getByRole("button", { name: "Wiederholen" });
    const moveFirst = () => fireEvent.keyDown(cellOf("w-counter-1"), { key: "ArrowRight" });

    beforeEach(() => {
      mockDashboard({ version: 3 });
      // like the server: the saved dashboard is what the next GET returns
      save = vi.spyOn(dashboardsApi, "save").mockImplementation(async (id, body) => {
        const saved = demoDashboard({ ...dashboards.get(id), version: body.version + 1, widgets: body.widgets });
        dashboards.set(id, saved);
        return saved;
      }) as never;
      previewData = vi.spyOn(dashboardsApi, "previewData").mockResolvedValue(ok()) as never;
    });

    it("Anpassen shows the dark toolbar, the column guide, handles and the keyboard hint; view-only chrome disappears", async () => {
      const bar = await enterEdit();
      expect(bar).toHaveTextContent("Mein Start bearbeiten");
      expect(bar).toHaveClass("bg-ink", "text-surface");
      expect(bar).toHaveFocus();
      expect(screen.getByTestId("dashboard-grid")).toHaveAttribute("data-editing", "true");
      expect(screen.getAllByTestId(/-handle$/)).toHaveLength(11);
      expect(screen.getAllByTestId(/-resize$/)).toHaveLength(11);
      expect(
        screen.getByText(/Pfeiltasten verschieben, Umschalt \+ Pfeiltasten ändern die Größe, Eingabe öffnet die Einstellungen/),
      ).toBeVisible();
      expect(undoButton()).toBeDisabled();
      expect(redoButton()).toBeDisabled();
      expect(cellOf("w-list")).toHaveAttribute("aria-roledescription", "verschiebbares Widget");
      expect(cellOf("w-list")).toHaveAttribute("tabindex", "0");
      for (const name of ["Anpassen", "Teilen", "Neues Dashboard"]) expect(screen.queryByRole("button", { name })).toBeNull();
      expect(screen.queryByRole("tablist")).toBeNull();
      expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    });

    it("dragging a widget by its handle shows the lifted widget and the snapped target, then commits one move and one undo step", async () => {
      await enterEdit();
      mockGridRect(1200);
      firePointer(screen.getByTestId("widget-w-list-handle"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointermove", { clientX: 10 - 4 * STEP_X, clientY: 10 + 5 });
      expect(screen.getByTestId("drop-target")).toHaveStyle({ gridColumn: "3 / span 6", gridRow: "6 / span 5" });
      expect(cellOf("w-list")).toHaveStyle({ opacity: "0.96" });
      expect(cellOf("w-list").style.transform).toContain(`translate(${10 - 4 * STEP_X - 10}px, 5px)`);
      expect(cellOf("w-list").style.gridColumn).toBe("7 / span 6"); // stays in its slot while lifted
      expect(rowStart("w-list-new")).toBe(11); // the others shift live
      expect(undoButton()).toBeDisabled();
      firePointer(window, "pointerup");
      expect(screen.queryByTestId("drop-target")).toBeNull();
      expect(cellOf("w-list").style.gridColumn).toBe("3 / span 6");
      expect(cellOf("w-list").style.gridRow).toBe("6 / span 5");
      expect(cellOf("w-list").style.transform).toBe("");
      fireEvent.click(undoButton());
      expect(cellOf("w-list").style.gridColumn).toBe("7 / span 6");
      expect(undoButton()).toBeDisabled(); // exactly one step
    });

    it("with prefers-reduced-motion the lifted widget follows the pointer without the tilt", async () => {
      vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
      await enterEdit();
      mockGridRect(1200);
      firePointer(screen.getByTestId("widget-w-list-handle"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointermove", { clientX: 60, clientY: 30 });
      expect(cellOf("w-list").style.transform).toBe("translate(50px, 20px)");
      firePointer(window, "pointerup");
    });

    it("without it the lifted widget is slightly tilted", async () => {
      await enterEdit();
      mockGridRect(1200);
      firePointer(screen.getByTestId("widget-w-list-handle"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointermove", { clientX: 60, clientY: 30 });
      expect(cellOf("w-list").style.transform).toContain("rotate(-0.6deg)");
      firePointer(window, "pointerup");
    });

    it("Escape during a drag aborts without history", async () => {
      await enterEdit();
      mockGridRect(1200);
      firePointer(screen.getByTestId("widget-w-list-handle"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointermove", { clientX: 300, clientY: 300 });
      expect(screen.getByTestId("drop-target")).toBeVisible();
      fireEvent.keyDown(window, { key: "Escape" });
      firePointer(window, "pointerup");
      expect(screen.queryByTestId("drop-target")).toBeNull();
      expect(undoButton()).toBeDisabled();
      expect(cellOf("w-list").style.gridColumn).toBe("7 / span 6");
      expect(screen.getByRole("toolbar", { name: "Bearbeiten" })).toBeVisible(); // Escape does not leave edit mode
    });

    it("resizing via the corner handle respects min and max and pushes the widget below", async () => {
      await enterEdit();
      mockGridRect(1200);
      firePointer(screen.getByTestId("widget-w-counter-1-resize"), "pointerdown", { clientX: 300, clientY: 130 });
      firePointer(window, "pointermove", { clientX: 300 + 6 * STEP_X, clientY: 130 + 5 * STEP_Y });
      firePointer(window, "pointerup");
      // counter: min [2,2], max [4,3]
      expect(cellOf("w-counter-1").style.gridColumn).toBe("1 / span 4");
      expect(cellOf("w-counter-1").style.gridRow).toBe("1 / span 3");
      expect(rowStart("w-esc")).toBeGreaterThanOrEqual(4); // y >= 3: pushed down, no overlap
    });

    it("keyboard: arrows move one cell, Shift+arrows resize, Enter opens the settings, with a live announcement", async () => {
      await enterEdit();
      const w = cellOf("w-counter-1");
      w.focus();
      fireEvent.keyDown(w, { key: "ArrowRight" });
      expect(w.style.gridColumn).toBe("2 / span 3");
      fireEvent.keyDown(w, { key: "ArrowDown", shiftKey: true });
      expect(w.style.gridRow).toBe("1 / span 3");
      expect(screen.getByRole("status")).toHaveTextContent(/„Meine offenen“ ist jetzt in Spalte 2, Zeile 1, 3 mal 3/);
      expect(w).toHaveFocus();
      fireEvent.keyDown(w, { key: "Enter" });
      const dialog = screen.getByRole("dialog", { name: /Einstellungen/ });
      expect(dialog).toBeVisible();
      expect(dialog).toHaveTextContent("Meine offenen");
    });

    it("per-widget settings, duplicate and remove buttons act on the session only (nothing is saved yet)", async () => {
      await enterEdit();
      fireEvent.click(within(cellOf("w-list")).getByRole("button", { name: "Duplizieren" }));
      expect(order()).toHaveLength(12);
      expect(order().indexOf("w-list")).toBe(6);
      fireEvent.click(within(cellOf("w-list")).getByRole("button", { name: "Entfernen" }));
      expect(order()).toHaveLength(11);
      expect(order()).not.toContain("w-list");
      expect(screen.getByRole("status")).toHaveTextContent("„Meine offenen Tickets“ entfernt");
      // focus moves to the next widget (the copy made a moment ago)
      expect(screen.getByTestId(`widget-${order()[6]}`)).toHaveFocus();
      expect(order()[6]).not.toBe("w-list-inbox");
      fireEvent.click(within(cellOf("w-esc")).getByRole("button", { name: "Einstellungen" }));
      expect(screen.getByRole("dialog", { name: /Einstellungen/ })).toBeVisible();
      expect(save).not.toHaveBeenCalled();
    });

    it("undo and redo buttons and Ctrl/Cmd+Z / Shift+Ctrl/Cmd+Z mirror the reducer", async () => {
      await enterEdit();
      moveFirst();
      expect(cellOf("w-counter-1").style.gridColumn).toBe("2 / span 3");
      fireEvent.click(undoButton());
      expect(cellOf("w-counter-1").style.gridColumn).toBe("1 / span 3");
      expect(redoButton()).toBeEnabled();
      fireEvent.keyDown(document.body, { key: "z", ctrlKey: true, shiftKey: true });
      expect(cellOf("w-counter-1").style.gridColumn).toBe("2 / span 3");
      fireEvent.keyDown(document.body, { key: "z", metaKey: true });
      expect(cellOf("w-counter-1").style.gridColumn).toBe("1 / span 3");
      fireEvent.keyDown(document.body, { key: "y", ctrlKey: true });
      expect(cellOf("w-counter-1").style.gridColumn).toBe("2 / span 3");
    });

    it("the draft is written 500 ms after the last change and the toolbar shows 'Entwurf gesichert · 10:42'", async () => {
      await enterEdit();
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
      vi.setSystemTime(new Date("2026-10-10T08:42:00Z")); // 10:42 in Europe/Berlin
      moveFirst();
      await act(() => vi.advanceTimersByTimeAsync(499));
      expect(localStorage.getItem(KEY)).toBeNull();
      expect(screen.queryByText(/Entwurf gesichert/)).toBeNull();
      await act(() => vi.advanceTimersByTimeAsync(2));
      expect(JSON.parse(localStorage.getItem(KEY)!).baseVersion).toBe(3);
      expect(screen.getByText("Entwurf gesichert · 10:42")).toBeVisible();
    });

    it("blocked localStorage: editing works, no draft text, no crash", async () => {
      vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
        throw new Error("blocked");
      });
      await enterEdit();
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
      moveFirst();
      await act(() => vi.advanceTimersByTimeAsync(600));
      expect(cellOf("w-counter-1").style.gridColumn).toBe("2 / span 3"); // the edit itself worked
      expect(screen.queryByText(/Entwurf gesichert/)).toBeNull(); // no false "saved" claim
      expect(screen.getByRole("toolbar", { name: "Bearbeiten" })).toBeVisible();
    });

    it("re-entering edit mode offers to restore a draft of the same base version and silently drops one of an older version", async () => {
      const draftWidgets = [DEFAULT_WIDGETS[0], DEFAULT_WIDGETS[6]].map((w) => ({
        id: w.id,
        type_key: w.type_key,
        type_version: w.type_version,
        title_override: null,
        settings: w.settings ?? {},
        layout: { lg: { ...w.layout.lg, y: 0, x: w.id === "w-list" ? 6 : 0 } },
      }));
      const seed = (baseVersion: number) =>
        localStorage.setItem(KEY, JSON.stringify({ baseVersion, savedAt: Date.parse("2026-10-10T08:42:00Z"), name: "Mein Start", widgets: draftWidgets }));
      seed(3);
      await enterEdit();
      fireEvent.click(await screen.findByRole("button", { name: "Entwurf von 10:42 wiederherstellen" }));
      expect(order()).toEqual(["w-counter-1", "w-list"]);
      expect(cellOf("w-list").style.gridRow).toBe("1 / span 5");
      expect(undoButton()).toBeEnabled(); // restoring is one undoable step
      fireEvent.click(screen.getByRole("button", { name: "Verwerfen" }));
      fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Änderungen verwerfen" }));
      expect(await screen.findByRole("button", { name: "Anpassen" })).toBeVisible();
      seed(2);
      fireEvent.click(screen.getByRole("button", { name: "Anpassen" }));
      await screen.findByRole("toolbar", { name: "Bearbeiten" });
      expect(screen.queryByText(/Entwurf von/)).toBeNull();
      expect(localStorage.getItem(KEY)).toBeNull();
    });

    it("Verwerfen with changes asks first, then clears the draft and restores the saved state; without changes it leaves directly", async () => {
      await enterEdit();
      fireEvent.click(screen.getByRole("button", { name: "Verwerfen" }));
      expect(screen.queryByRole("alertdialog")).toBeNull();
      expect(screen.queryByRole("toolbar")).toBeNull(); // a clean session leaves at once
      expect(screen.getByRole("button", { name: "Anpassen" })).toHaveFocus();
      fireEvent.click(screen.getByRole("button", { name: "Anpassen" }));
      await screen.findByRole("toolbar", { name: "Bearbeiten" });
      moveFirst();
      localStorage.setItem(KEY, "{}"); // as if the autosave had run
      fireEvent.click(screen.getByRole("button", { name: "Verwerfen" }));
      const confirm = screen.getByRole("alertdialog", { name: "Änderungen verwerfen?" });
      fireEvent.click(within(confirm).getByRole("button", { name: "Weiter bearbeiten" }));
      expect(screen.getByRole("toolbar", { name: "Bearbeiten" })).toBeVisible();
      fireEvent.click(screen.getByRole("button", { name: "Verwerfen" }));
      fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Änderungen verwerfen" }));
      expect(localStorage.getItem(KEY)).toBeNull();
      expect(screen.queryByRole("toolbar")).toBeNull();
      expect(cellOf("w-counter-1").style.gridColumn).toBe("1 / span 3");
    });

    it("Speichern sends widgets in array order with the base version, clears the draft and returns to view mode", async () => {
      await enterEdit();
      moveFirst();
      localStorage.setItem(KEY, "{}");
      fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
      await waitFor(() => expect(screen.queryByRole("toolbar")).toBeNull());
      expect(save).toHaveBeenCalledTimes(1);
      const [id, body] = save.mock.calls[0];
      expect(id).toBe(1);
      expect(body).toMatchObject({ name: "Mein Start", icon: null, version: 3 });
      expect(body.widgets.map((w: { id: string }) => w.id)).toEqual(IDS);
      expect(body.widgets[0]).toEqual({
        id: "w-counter-1",
        type_key: "core.counter",
        type_version: 1,
        title_override: null,
        settings: DEFAULT_WIDGETS[0].settings,
        layout: { lg: { x: 1, y: 0, w: 3, h: 2 } },
      });
      expect(localStorage.getItem(KEY)).toBeNull();
      expect(cellOf("w-counter-1").style.gridColumn).toBe("2 / span 3");
      expect(screen.getByRole("button", { name: "Anpassen" })).toHaveFocus();
    });

    it("a layout the server would reject as too tall is not sent", async () => {
      mockDashboard({ version: 3, widgets: [wire("w-tall", "core.notes", { lg: { x: 0, y: 999, w: 4, h: 2 } })] });
      await enterEdit();
      fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
      expect(await within(screen.getByRole("toolbar")).findByRole("alert")).toHaveTextContent("Das Layout ist zu hoch.");
      expect(save).not.toHaveBeenCalled();
    });

    it("conflict keeps the session and the draft and offers reload or continue; reload keeps the draft for the new version", async () => {
      save.mockRejectedValue(new DashboardConflictError("Dieses Dashboard wurde inzwischen geändert.", 4));
      await enterEdit();
      moveFirst();
      localStorage.setItem(KEY, JSON.stringify({ baseVersion: 3 }));
      fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
      const dialog = await screen.findByRole("alertdialog", { name: /inzwischen geändert/ });
      expect(localStorage.getItem(KEY)).not.toBeNull();
      expect(screen.getByRole("toolbar", { name: "Bearbeiten" })).toBeVisible();
      fireEvent.click(within(dialog).getByRole("button", { name: "Weiter bearbeiten" }));
      expect(screen.queryByRole("alertdialog")).toBeNull();
      expect(cellOf("w-counter-1").style.gridColumn).toBe("2 / span 3");
      const gets = apiMock.get.mock.calls.length;
      fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
      fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Neu laden" }));
      await waitFor(() => expect(screen.queryByRole("toolbar")).toBeNull());
      await waitFor(() => expect(apiMock.get.mock.calls.length).toBeGreaterThan(gets));
      const stored = JSON.parse(localStorage.getItem(KEY)!);
      expect(stored.baseVersion).toBe(4);
      expect(stored.widgets[0].layout.lg.x).toBe(1);
    });

    async function conflictThenReload() {
      // the other save: version 4 with one widget fewer
      save.mockRejectedValueOnce(new DashboardConflictError("Dieses Dashboard wurde inzwischen geändert.", 4));
      await enterEdit();
      moveFirst();
      dashboards.set(1, demoDashboard({ ...dashboards.get(1)!, version: 4, widgets: DEFAULT_WIDGETS.slice(0, 10) }));
      fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
      const dialog = await screen.findByRole("alertdialog", { name: /inzwischen geändert/ });
      expect(dialog).toHaveTextContent("Deine Änderungen bleiben als Entwurf erhalten");
      expect(dialog).toHaveTextContent("ersetzen sie die neue Fassung dann vollständig");
      fireEvent.click(within(dialog).getByRole("button", { name: "Neu laden" }));
      await waitFor(() => expect(order()).toHaveLength(10)); // the newer version is shown
    }

    it("after a conflict, restoring the kept draft says it replaces the newer version and saving asks first", async () => {
      await conflictThenReload();
      fireEvent.click(screen.getByRole("button", { name: "Anpassen" }));
      const offer = await screen.findByRole("button", { name: /wiederherstellen – ersetzt beim Speichern die neuere Fassung \(Stand \d\d:\d\d\)/ });
      expect(offer).toHaveTextContent(/^Deine Änderungen von \d\d:\d\d wiederherstellen/);
      fireEvent.click(offer);
      expect(order()).toHaveLength(11);
      fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
      const confirm = screen.getByRole("alertdialog", { name: "Neuere Fassung ersetzen?" });
      expect(confirm).toHaveTextContent("ersetzt diese Fassung vollständig durch deine");
      fireEvent.click(within(confirm).getByRole("button", { name: "Weiter bearbeiten" }));
      expect(save).toHaveBeenCalledTimes(1); // only the conflicting attempt
      fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
      fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Ersetzen und speichern" }));
      await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
      expect(save.mock.calls[1][1]).toMatchObject({ version: 4 });
      expect(save.mock.calls[1][1].widgets).toHaveLength(11);
      await waitFor(() => expect(screen.queryByRole("toolbar")).toBeNull());
      expect(localStorage.getItem(KEY)).toBeNull();
    });

    it("a normal draft restores and saves without the replace question", async () => {
      localStorage.setItem(
        KEY,
        JSON.stringify({ baseVersion: 3, savedAt: Date.parse("2026-10-10T08:42:00Z"), name: "Mein Start", widgets: DEFAULT_WIDGETS.slice(0, 3) }),
      );
      await enterEdit();
      fireEvent.click(screen.getByRole("button", { name: "Entwurf von 10:42 wiederherstellen" }));
      fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
      await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });

    it("for a role template the replace question says that other people's changes go for every role member", async () => {
      mockDashboard({ version: 3, kind: "template", is_owner: false });
      await conflictThenReload();
      fireEvent.click(screen.getByRole("button", { name: "Anpassen" }));
      fireEvent.click(await screen.findByRole("button", { name: /ersetzt beim Speichern die neuere Fassung/ }));
      fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
      expect(screen.getByRole("alertdialog", { name: "Neuere Fassung ersetzen?" })).toHaveTextContent(
        "Änderungen anderer gehen verloren, und das gilt für alle, die die Vorlage über ihre Rolle bekommen",
      );
      expect(save).toHaveBeenCalledTimes(1);
    });

    it("opening another dashboard by link ends edit mode (it never opens in edit mode by itself)", async () => {
      await enterEdit();
      moveFirst();
      await act(async () => {
        await (currentRouter as unknown as { navigate: (o: object) => Promise<void> }).navigate({ to: "/agent", search: { d: 2 } });
      });
      await waitFor(() => expect(screen.queryByRole("toolbar")).toBeNull());
      expect(await screen.findByTestId("widget-t-team")).toBeVisible();
      expect(JSON.parse(localStorage.getItem(KEY)!).widgets[0].layout.lg.x).toBe(1); // the change is kept as a draft
    });

    it("a 422 with a widget path outlines that widget and opens its settings", async () => {
      save.mockRejectedValue(
        new ApiError(422, { detail: { message: "Der Wert muss zwischen 1 und 50 liegen.", path: "widgets[6].settings.limit" } }, "/api/v1/dashboards/1"),
      );
      await enterEdit();
      fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
      const dialog = await screen.findByRole("dialog", { name: /Einstellungen/ });
      expect(dialog).toBeVisible();
      expect(cellOf("w-list")).toHaveClass("ring-danger");
      expect(within(dialog).getByText("Der Wert muss zwischen 1 und 50 liegen.")).toBeVisible();
      expect(screen.getByRole("toolbar", { name: "Bearbeiten" })).toBeVisible();
    });

    it("entering edit mode reuses the view's data: no preview request, no second batch; a duplicate with the same settings reuses it too", async () => {
      await enterEdit();
      expect(within(cellOf("w-counter-1")).getByText("12")).toBeVisible();
      moveFirst();
      fireEvent.click(within(cellOf("w-list")).getByRole("button", { name: "Duplizieren" }));
      await act(async () => {
        await new Promise((r) => setTimeout(r, 350));
      });
      expect(previewData).not.toHaveBeenCalled();
      expect(apiMock.data).toHaveBeenCalledTimes(1); // only the pre-edit view load
      expect(apiMock.widgetData).not.toHaveBeenCalled();
    });

    it("an unknown widget type stays in the grid in edit mode and is saved back unchanged when untouched", async () => {
      mockDashboard({ version: 3, widgets: [wire("g1", "org.example.gone", { lg: { x: 0, y: 0, w: 4, h: 2 } }, { a: 1 })] });
      await enterEdit();
      expect(within(cellOf("g1")).getByRole("button", { name: "Entfernen" })).toBeVisible();
      expect(screen.getByTestId("widget-g1-handle")).toBeVisible();
      fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
      await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
      expect(save.mock.calls[0][1].widgets[0]).toEqual({
        id: "g1",
        type_key: "org.example.gone",
        type_version: 1,
        title_override: null,
        settings: { a: 1 },
        layout: { lg: { x: 0, y: 0, w: 4, h: 2 } },
      });
    });

    // ---- phone
    it("on a phone-width container edit mode offers only up/down and settings per widget — no gallery, drag, duplicate or remove", async () => {
      containerWidth = 390;
      await enterEdit();
      const w = cellOf("w-list");
      expect(within(w).getByRole("button", { name: "Nach oben" })).toBeVisible();
      expect(within(w).getByRole("button", { name: "Nach unten" })).toBeVisible();
      expect(within(w).getByRole("button", { name: "Einstellungen" })).toBeVisible();
      for (const name of ["Duplizieren", "Entfernen", "Widget hinzufügen"]) expect(screen.queryByRole("button", { name })).toBeNull();
      expect(screen.queryByTestId("widget-w-list-handle")).toBeNull();
      expect(screen.queryByTestId(/-resize$/)).toBeNull();
      expect(screen.queryByTestId("counter-group")).toBeNull(); // every widget carries its own controls
      expect(screen.getByText(/die Reihenfolge lässt sich hier ändern/)).toBeVisible();
      const prevIndex = order().indexOf("w-list");
      fireEvent.click(within(w).getByRole("button", { name: "Nach oben" }));
      expect(order().indexOf("w-list")).toBe(prevIndex - 1);
      expect(within(cellOf("w-list")).getByRole("button", { name: "Nach oben" })).toHaveFocus();
      expect(screen.getByRole("status")).toHaveTextContent("ist jetzt an Stelle 6 von 11");
      fireEvent.click(undoButton());
      expect(order().indexOf("w-list")).toBe(prevIndex);
    });

    describe("widget gallery", () => {
      const WIDE = wire("w-wide", "core.notes", { lg: { x: 0, y: 0, w: 12, h: 2 } });
      const dropData = (payload: Record<string, string>) => ({
        types: Object.keys(payload),
        getData: (k: string) => payload[k] ?? "",
        dropEffect: "none",
      });
      /** jsdom has no DragEvent: build the drop and give it the pointer position by hand. */
      const drop = (payload: Record<string, string>, x: number, y: number) => {
        const ev = createEvent.drop(screen.getByTestId("dashboard-grid"), { dataTransfer: dropData(payload) });
        Object.defineProperty(ev, "clientX", { value: x });
        Object.defineProperty(ev, "clientY", { value: y });
        fireEvent(screen.getByTestId("dashboard-grid"), ev);
      };
      const MIME = "application/x-tiqora-widget";
      const ids = () => order().filter((id): id is string => !!id);
      const gcell = (id: string) =>
        screen.getByTestId("dashboard-grid").querySelector<HTMLElement>(`[data-widget-id="${id}"]`)!;
      const gallery = () => screen.getByRole("complementary", { name: "Widget hinzufügen" });

      it("is open beside the grid in edit mode with the groups and the plugin widget in 'Für mich'", async () => {
        i18n.addResourceBundle("de", "plugin.org.example.demo", { hello: "Hallo-Widget" }, true, true);
        DASHBOARD_WIDGETS.register(
          defineWidget({
            key: "org.example.demo.hello",
            owner: "org.example.demo",
            version: 1,
            titleKey: "hello",
            ns: "plugin.org.example.demo",
            category: "other",
            icon: "puzzle",
            size: { default: [4, 3], min: [2, 2], max: [12, 8] },
            settingsSchema: null,
            dataEndpoint: null,
            refreshOn: [],
            permission: null,
            component: () => null,
          }),
          "org.example.demo",
        );
        await enterEdit();
        expect(within(gallery()).getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["Tickets", "Für mich", "Team"]);
        expect(within(gallery()).getByRole("button", { name: /Hallo-Widget/ })).toBeVisible();
        expect(screen.getByRole("button", { name: "Widget hinzufügen", expanded: true })).toBeVisible();
      });

      it("a click adds the widget with a fresh uuid in the first free spot, as one undo step", async () => {
        mockDashboard({ widgets: [WIDE] });
        await enterEdit();
        fireEvent.click(within(gallery()).getByRole("button", { name: /Notizen/ }));
        expect(screen.getByText("„Notizen“ hinzugefügt – 2 von 40 Widgets")).toBeInTheDocument();
        fireEvent.click(within(gallery()).getByRole("button", { name: /Notizen/ }));
        // the same widget twice is announced twice (different text), and the new widget has the focus
        expect(screen.getByText("„Notizen“ hinzugefügt – 3 von 40 Widgets")).toBeInTheDocument();
        const added = ids().filter((id) => id !== "w-wide");
        expect(gcell(added[1])).toHaveFocus();
        expect(added).toHaveLength(2);
        expect(added[0]).toMatch(/^[0-9a-f-]{36}$/);
        expect(added[0]).not.toBe(added[1]);
        expect(gcell(added[0]).style.gridRow).toMatch(/^3 \//);
        expect(undoButton()).toBeEnabled();
        fireEvent.click(undoButton());
        expect(ids()).toHaveLength(2);
      });

      it("dropping a card on the grid adds the widget at the snapped cell", async () => {
        mockDashboard({ widgets: [WIDE] });
        await enterEdit();
        mockGridRect(1200);
        drop({ [MIME]: "core.counter" }, 205, 165);
        const added = ids().filter((id) => id !== "w-wide");
        expect(added).toHaveLength(1);
        expect(gcell(added[0]).style.gridColumn).toMatch(/^3 \//);
        expect(gcell(added[0]).style.gridRow).toMatch(/^3 \//);
        expect(undoButton()).toBeEnabled();
      });

      it("a drop with a foreign or unknown payload does nothing", async () => {
        mockDashboard({ widgets: [WIDE] });
        await enterEdit();
        mockGridRect(1200);
        drop({ "text/plain": "core.counter" }, 100, 100);
        drop({ [MIME]: "org.example.unknown" }, 100, 100);
        expect(ids()).toEqual(["w-wide"]);
        expect(undoButton()).toBeDisabled();
      });

      it("accepts the drag only for its own payload (dragover is cancelled for it, not for text)", async () => {
        await enterEdit();
        const grid = screen.getByTestId("dashboard-grid");
        expect(fireEvent.dragOver(grid, { dataTransfer: dropData({ [MIME]: "core.counter" }) })).toBe(false);
        expect(fireEvent.dragOver(grid, { dataTransfer: dropData({ "text/plain": "x" }) })).toBe(true);
      });

      it("at 40 widgets the cards are disabled, a drop adds nothing and the panel says so", async () => {
        const many = Array.from({ length: 40 }, (_, i) => wire(`m-${i}`, "core.notes", { lg: { x: 0, y: i, w: 12, h: 1 } }));
        mockDashboard({ widgets: many });
        await enterEdit();
        expect(screen.getByText("Es sind höchstens 40 Widgets möglich.")).toBeVisible();
        expect(within(gallery()).getByRole("button", { name: /Notizen/ })).toBeDisabled();
        mockGridRect(1200);
        drop({ [MIME]: "core.counter" }, 100, 100);
        expect(ids()).toHaveLength(40);
      });

      it("Escape closes it and the focus returns to the toolbar; the toolbar button opens it again with the search focused", async () => {
        const bar = await enterEdit();
        fireEvent.keyDown(within(gallery()).getByLabelText("Suchen"), { key: "Escape" });
        expect(screen.queryByRole("complementary", { name: "Widget hinzufügen" })).toBeNull();
        expect(bar).toContainElement(document.activeElement as HTMLElement);
        fireEvent.click(screen.getByRole("button", { name: "Widget hinzufügen", expanded: false }));
        expect(within(gallery()).getByLabelText("Suchen")).toHaveFocus();
      });

      it("on a phone there is no gallery and no button", async () => {
        containerWidth = 390;
        await enterEdit();
        expect(screen.queryByRole("complementary", { name: "Widget hinzufügen" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Widget hinzufügen" })).toBeNull();
      });
    });

    it("'Nach oben' on the first and 'Nach unten' on the last widget are disabled; arrow keys reorder on the phone", async () => {
      containerWidth = 390;
      await enterEdit();
      const all = Array.from(screen.getByTestId("dashboard-grid").querySelectorAll<HTMLElement>("[data-type]"));
      expect(within(all[0]).getByRole("button", { name: "Nach oben" })).toBeDisabled();
      expect(within(all[0]).getByRole("button", { name: "Nach unten" })).toBeEnabled();
      expect(within(all.at(-1)!).getByRole("button", { name: "Nach unten" })).toBeDisabled();
      fireEvent.keyDown(cellOf("w-counter-1"), { key: "ArrowDown" });
      expect(order().slice(0, 2)).toEqual(["w-counter", "w-counter-1"]);
      fireEvent.keyDown(cellOf("w-counter-1"), { key: "Enter" });
      expect(screen.getByRole("dialog", { name: /Einstellungen/ })).toBeVisible();
    });
  });
});
