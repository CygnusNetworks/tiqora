import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { buildDemoResult, demoReport, demoRunOptions } from "@/demo/reportsMock";
import { DASHBOARD_WIDGETS, type WidgetMode } from "@/plugins/dashboardWidgets";
import { settingsWithDefaults } from "../definitions";
import { WidgetDataProvider } from "../data";
import { WidgetChromeProvider } from "../chrome/WidgetFrame";
import { validateDrillSearch } from "../drill/drillLinks";
import { registerCoreWidgets } from "./index";
import type { ReportTileData } from "./ReportWidget";

const REPORT = demoReport("builtin.volume_by_queue")!;

function tile(drillable: string[]): ReportTileData {
  const result = { ...buildDemoResult(REPORT, demoRunOptions(REPORT)), report_key: REPORT.key };
  return {
    report: { key: REPORT.key, kind: "builtin", title_key: REPORT.title_key },
    display: "auto",
    deferred: false,
    definition: REPORT.definition,
    result,
    ignored_filters: [],
    drillable_dimensions: drillable,
  };
}

async function mount(drillable: string[], mode: WidgetMode) {
  const def = DASHBOARD_WIDGETS.get("core.report")!;
  const Component = def.component;
  const ui = (
    <WidgetChromeProvider value={{ dashboardId: 7 }}>
      <WidgetDataProvider value={{ status: "ok", data: tile(drillable), refetch: () => {} }}>
        <Component instanceId="w-report" settings={settingsWithDefaults(def, { report_key: REPORT.key }, 1)} size={{ w: 6, h: 5 }} filters={{}} mode={mode} />
      </WidgetDataProvider>
    </WidgetChromeProvider>
  );
  const root = createRootRoute({ component: () => ui });
  const drill = createRoute({ getParentRoute: () => root, path: "/agent/tickets/drill", validateSearch: validateDrillSearch, component: () => <p data-testid="drill">drill</p> });
  const router = createRouter({ routeTree: root.addChildren([drill]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  await router.load();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

const marks = () => screen.queryAllByRole("button").filter((b) => b.getAttribute("aria-label")?.includes(":"));

beforeAll(async () => {
  window.scrollTo = vi.fn();
  await i18n.changeLanguage("de");
  registerCoreWidgets();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("report widget drill-down", () => {
  it("a bar of a drillable dimension opens the drill page with the widget id and the raw point", async () => {
    const router = await mount(["queue"], "view");
    const bars = await waitFor(() => {
      const found = marks();
      expect(found.length).toBeGreaterThan(0);
      return found;
    });
    fireEvent.click(bars[0]);
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/tickets/drill"));
    const search = router.state.location.search as { d: number; w: string; point: Record<string, unknown> };
    expect(search.d).toBe(7);
    expect(search.w).toBe("w-report");
    expect(Object.keys(search.point)).toEqual(["queue"]);
    expect(router.state.location.href).not.toContain("filter");
  });

  it("no marks are buttons when the answer lists no drillable dimension, nor in edit or preview mode", async () => {
    await mount([], "view");
    expect(marks()).toHaveLength(0);
    cleanup();
    for (const mode of ["edit", "preview"] as const) {
      await mount(["queue"], mode);
      expect(marks()).toHaveLength(0);
      cleanup();
    }
  });
});
