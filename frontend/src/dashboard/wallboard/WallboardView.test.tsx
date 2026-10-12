import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { DASHBOARD_WIDGETS } from "@/plugins/dashboardWidgets";
import { demoDefaultWidgets, demoWidgetData } from "@/demo/dashboardsMock";
import { registerCoreWidgets } from "../widgets";
import { toEditWidget } from "../model";
import type { WidgetDataState } from "../data";
import { KioskProvider } from "./kioskContext";
import { WallboardView, type WallboardViewProps } from "./WallboardView";

vi.mock("@/auth/AuthContext", async () => {
  const actual = await vi.importActual<typeof import("@/auth/AuthContext")>("@/auth/AuthContext");
  return { ...actual, useOptionalAuth: () => ({ user: { id: 5, is_admin: false, can_view_reports: false } }) };
});
vi.mock("@/plugins/PluginProvider", async () => {
  const actual = await vi.importActual<typeof import("@/plugins/PluginProvider")>("@/plugins/PluginProvider");
  return { ...actual, usePlugins: () => ({ plugins: [], ready: true, safeMode: false }) };
});

const widgets = demoDefaultWidgets().map((w, i) => toEditWidget({ ...w, id: `w${i}` }));
const data: Record<string, WidgetDataState> = Object.fromEntries(
  widgets.map((w) => {
    const r = demoWidgetData(w.type_key, w.settings);
    return [w.id, r ? { status: r.status, data: r.data ?? undefined } : { status: "none" as const }];
  }),
);

function setViewport(width: number, height: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: height });
}

function mockMotion(reduce: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: reduce && q.includes("reduce"),
    media: q,
    addEventListener() {},
    removeEventListener() {},
  }));
}

async function mount(props: Partial<WallboardViewProps> = {}, kiosk = false) {
  const view = (
    <WallboardView
      name="Leitstand"
      widgets={widgets}
      data={data}
      filters={{ queue_ids: [], time_range: null }}
      generatedAt={new Date().toISOString()}
      stale={false}
      onRetry={() => {}}
      {...props}
    />
  );
  const rootRoute = createRootRoute({ component: () => (kiosk ? <KioskProvider>{view}</KioskProvider> : view) });
  const router = createRouter({ routeTree: rootRoute, history: createMemoryHistory({ initialEntries: ["/"] }) });
  await act(async () => {
    await router.load();
  });
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
beforeEach(() => {
  DASHBOARD_WIDGETS.clearForTests();
  registerCoreWidgets();
  setViewport(1920, 1080);
  mockMotion(false);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  DASHBOARD_WIDGETS.clearForTests();
});

describe("WallboardView", () => {
  it("forces the dark theme on its own container and fits the board into the viewport", async () => {
    await mount();
    const root = screen.getByTestId("wallboard");
    expect(root).toHaveAttribute("data-theme", "dark");
    expect(root).toHaveClass("overflow-hidden");
    const rowHeight = Number(root.getAttribute("data-row-height"));
    expect(rowHeight).toBeGreaterThanOrEqual(48);
    expect(rowHeight).toBeLessThanOrEqual(160);
    const grid = screen.getByTestId("dashboard-grid");
    expect(grid.style.gridAutoRows).toBe(`${rowHeight}px`);
    expect(grid.style.gap).toBe("24px");
    expect(root.style.getPropertyValue("--wb-scale")).toBe(String(rowHeight / 64));
  });

  it("refits when the window changes", async () => {
    await mount({ widgets: widgets.slice(0, 5) });
    const before = Number(screen.getByTestId("wallboard").getAttribute("data-row-height"));
    act(() => {
      setViewport(1280, 720);
      window.dispatchEvent(new Event("resize"));
    });
    const after = Number(screen.getByTestId("wallboard").getAttribute("data-row-height"));
    expect(after).toBeLessThan(before);
  });

  it("renders every widget in wallboard mode", async () => {
    await mount();
    expect(screen.getAllByTestId("counter-wallboard").length).toBeGreaterThan(0);
    expect(screen.getByTestId("queue-overview-wallboard")).toBeInTheDocument();
  });

  it("cross-fades in over 300 ms", async () => {
    await mount();
    const root = screen.getByTestId("wallboard");
    expect(root).toHaveClass("transition-opacity", "duration-300");
    expect(root).toHaveClass("opacity-0"); // starts transparent ...
    await waitFor(() => expect(root).toHaveClass("opacity-100")); // ... and fades in
  });

  it("reduced motion: no crossfade, visible at once", async () => {
    mockMotion(true);
    await mount();
    const root = screen.getByTestId("wallboard");
    expect(root).not.toHaveClass("transition-opacity");
    expect(root).toHaveClass("opacity-100");
  });

  it("a kiosk view has no link anywhere", async () => {
    await mount({ dashboardId: 7 }, true);
    expect(screen.queryAllByRole("link", { hidden: true })).toHaveLength(0);
  });

  it("shows the stale banner in the header", async () => {
    await mount({ stale: true, generatedAt: "2026-10-10T10:41:00" });
    expect(screen.getByTestId("wallboard-status")).toHaveTextContent(/^Verbindung unterbrochen – Stand \d\d:\d\d$/);
  });
});
