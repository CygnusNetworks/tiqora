import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
});
