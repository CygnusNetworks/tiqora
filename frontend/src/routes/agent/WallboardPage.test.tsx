import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { dashboardsApi, type Dashboard, type DashboardWidgetRow, type WidgetResult } from "@/lib/dashboardsApi";
import { demoDashboard, demoTab, demoWidgetData } from "@/demo/dashboardsMock";
import { validateWallboardSearch } from "@/dashboard/wallboard/search";
import { WallboardPage } from "./WallboardPage";

vi.mock("@/auth/AuthContext", async () => {
  const actual = await vi.importActual<typeof import("@/auth/AuthContext")>("@/auth/AuthContext");
  const auth = { user: { id: 5, login: "anna", first_name: "Anna", is_admin: false, can_view_reports: false } };
  return { ...actual, useAuth: () => auth, useOptionalAuth: () => auth };
});
vi.mock("@/plugins/PluginProvider", async () => {
  const actual = await vi.importActual<typeof import("@/plugins/PluginProvider")>("@/plugins/PluginProvider");
  return { ...actual, usePlugins: () => ({ plugins: [], ready: true, safeMode: false }) };
});

function counter(id: string): DashboardWidgetRow {
  return {
    id,
    type_key: "core.counter",
    type_version: 1,
    title_override: null,
    settings: { source: { kind: "preset", preset: "mine_open" } },
    layout: { lg: { x: 0, y: 0, w: 3, h: 2 } },
    pinned: false,
  };
}

const DASHBOARDS: Record<number, Dashboard> = {
  1: demoDashboard({ id: 1, name: "Leitstand", widgets: [counter("a")] }),
  2: demoDashboard({ id: 2, name: "Support", widgets: [counter("b")] }),
  3: demoDashboard({ id: 3, name: "Kaputt", widgets: [counter("c")] }),
};

let failData: Set<number>;
let failGet: Set<number>;
let client: QueryClient;

async function mount(url: string) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute();
  const wall = createRoute({ getParentRoute: () => rootRoute, path: "/agent/wallboard", validateSearch: validateWallboardSearch, component: WallboardPage });
  const home = createRoute({ getParentRoute: () => rootRoute, path: "/agent", component: () => <p>Startseite</p> });
  const targets = ["/agent/tickets/drill", "/agent/tickets/$ticketId", "/agent/queues", "/agent/calendar"].map((path) =>
    createRoute({ getParentRoute: () => rootRoute, path }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren([wall, home, ...targets]),
    history: createMemoryHistory({ initialEntries: [url] }),
  });
  await act(async () => {
    await router.load();
  });
  render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  // settle chained queries (list -> dashboard -> data) a few ticks at a time
  for (let i = 0; i < 8; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5);
    });
  }
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  vi.setSystemTime(new Date("2026-10-10T10:00:00Z"));
  failData = new Set();
  failGet = new Set();
  window.scrollTo = vi.fn() as never;
  vi.spyOn(dashboardsApi, "list").mockResolvedValue(Object.values(DASHBOARDS).map(({ widgets: _w, ...tab }) => demoTab(tab)));
  vi.spyOn(dashboardsApi, "get").mockImplementation(async (id: number) => {
    if (failGet.has(id)) throw new ApiError(404, { detail: "weg" }, `/api/v1/dashboards/${id}`);
    return DASHBOARDS[id];
  });
  vi.spyOn(dashboardsApi, "data").mockImplementation(async (id: number) => {
    if (failData.has(id)) throw new Error("offline");
    const out: Record<string, WidgetResult> = {};
    for (const w of DASHBOARDS[id].widgets) {
      const r = demoWidgetData(w.type_key, w.settings ?? {});
      if (r) out[w.id] = r;
    }
    return { widgets: out };
  });
  vi.spyOn(dashboardsApi, "widgetData").mockResolvedValue({ status: "ok", data: { value: 1, delta: null, spark: [] } });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const heading = () => screen.getByRole("heading", { level: 1 }).textContent;

describe("WallboardPage (TV mode)", () => {
  it("shows the first dashboard with its position, rotates every N seconds and wraps", async () => {
    await mount("/agent/wallboard?ids=1,2&rotate=15");
    await flush();
    expect(heading()).toBe("Leitstand");
    expect(screen.getByTestId("wallboard-status")).toHaveTextContent("1 / 2");
    await flush(15_000);
    expect(heading()).toBe("Support");
    expect(screen.getByTestId("wallboard-status")).toHaveTextContent("2 / 2");
    await flush(15_000);
    expect(heading()).toBe("Leitstand");
  });

  it("without ids it rotates through all dashboards of the agent", async () => {
    await mount("/agent/wallboard?rotate=15");
    await flush();
    expect(heading()).toBe("Leitstand");
    await flush(15_000);
    expect(heading()).toBe("Support");
    await flush(15_000);
    expect(heading()).toBe("Kaputt");
  });

  it("keeps rotating when one dashboard's data fails: the others stay fine", async () => {
    failData.add(2);
    await mount("/agent/wallboard?ids=1,2,3&rotate=15");
    await flush();
    expect(heading()).toBe("Leitstand");
    expect(screen.getByTestId("counter-wallboard")).toBeInTheDocument();
    await flush(15_000);
    expect(heading()).toBe("Support");
    expect(screen.queryByTestId("counter-wallboard")).toBeNull(); // its tile shows the error, not data
    expect(screen.getByRole("alert")).toHaveTextContent("Diese Daten konnten nicht geladen werden.");
    await flush(15_000);
    expect(heading()).toBe("Kaputt");
    expect(screen.getByTestId("counter-wallboard")).toBeInTheDocument();
  });

  it("a dashboard that no longer exists shows a message and the rotation goes on", async () => {
    failGet.add(2);
    await mount("/agent/wallboard?ids=1,2,3&rotate=15");
    await flush();
    await flush(15_000);
    expect(screen.getByTestId("wallboard-unavailable")).toHaveTextContent("Dieses Dashboard konnte nicht geladen werden.");
    await flush(15_000);
    expect(heading()).toBe("Kaputt");
  });

  it("stale data (> 3 refresh intervals) shows 'Verbindung unterbrochen' until a refresh succeeds", async () => {
    await mount("/agent/wallboard?ids=1");
    await flush();
    expect(screen.getByTestId("wallboard-status")).not.toHaveAttribute("data-stale");
    failData.add(1);
    await flush(100_000); // the 30 s refreshes fail; the picture is older than 90 s
    const status = screen.getByTestId("wallboard-status");
    expect(status).toHaveAttribute("data-stale", "true");
    expect(status).toHaveTextContent(/^Verbindung unterbrochen – Stand \d\d:\d\d$/);
    expect(screen.getByTestId("counter-wallboard")).toBeInTheDocument(); // the last picture stays
    failData.delete(1);
    await flush(30_000);
    expect(screen.getByTestId("wallboard-status")).not.toHaveAttribute("data-stale");
  });

  it("controls appear on pointer move only and 'Beenden' returns to the dashboard", async () => {
    const router = await mount("/agent/wallboard?ids=1");
    await flush();
    const controls = screen.getByTestId("wallboard-controls");
    expect(controls).toHaveClass("opacity-0");
    expect(controls).toHaveAttribute("aria-hidden", "true");
    fireEvent.pointerMove(screen.getByTestId("wallboard-page"));
    expect(controls).toHaveClass("opacity-100");
    expect(screen.getByRole("button", { name: "Vollbild" })).toBeVisible();
    await flush(3_100);
    expect(controls).toHaveClass("opacity-0");
    fireEvent.pointerMove(screen.getByTestId("wallboard-page"));
    fireEvent.click(screen.getByRole("button", { name: "Beenden" }));
    await flush();
    expect(router.state.location.pathname).toBe("/agent");
    expect(screen.getByText("Startseite")).toBeVisible();
  });

  it("asks the browser for fullscreen and a wake lock, and ignores failures", async () => {
    const request = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(document.documentElement, "requestFullscreen", { configurable: true, value: request });
    const lock = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "wakeLock", { configurable: true, value: { request: lock } });
    await mount("/agent/wallboard?ids=1");
    await flush();
    expect(lock).toHaveBeenCalledWith("screen");
    fireEvent.pointerMove(screen.getByTestId("wallboard-page"));
    fireEvent.click(screen.getByRole("button", { name: "Vollbild" }));
    expect(request).toHaveBeenCalled();
    await flush();
    expect(heading()).toBe("Leitstand");
    delete (navigator as unknown as Record<string, unknown>).wakeLock;
  });
});
