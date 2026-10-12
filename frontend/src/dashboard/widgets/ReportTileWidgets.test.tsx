import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { dashboardsApi } from "@/lib/dashboardsApi";
import { jobsApi, type Job } from "@/lib/jobsApi";
import { reportsApi, type ReportItem } from "@/lib/reportsApi";
import { demoReport } from "@/demo/reportsMock";
import { demoWidgetData } from "@/demo/dashboardsMock";
import { DASHBOARD_WIDGETS, type WidgetMode } from "@/plugins/dashboardWidgets";
import { canUseWidget, settingsWithDefaults } from "../definitions";
import { WidgetDataProvider, type WidgetDataValue } from "../data";
import { WidgetChromeProvider } from "../chrome/WidgetFrame";
import { DashboardFiltersProvider } from "../filters/filtersContext";
import { registerCoreWidgets } from "./index";
import { gaugeFigures } from "./gaugeFigures";

let client: QueryClient;
type State = Omit<WidgetDataValue, "refetch">;

function mount(key: string, state: State, opts: { settings?: Record<string, unknown>; mode?: WidgetMode; size?: { w: number; h: number } } = {}) {
  const def = DASHBOARD_WIDGETS.get(key)!;
  const Component = def.component;
  const refetch = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <DashboardFiltersProvider filters={{ queue_ids: [], time_range: null }}>
          <WidgetChromeProvider value={{ dashboardId: 1 }}>
            <WidgetDataProvider value={{ ...state, refetch }}>
              <Component
                instanceId="w-1"
                settings={settingsWithDefaults(def, opts.settings ?? {}, 1)}
                size={opts.size ?? { w: 6, h: 5 }}
                filters={{}}
                mode={opts.mode ?? "view"}
              />
            </WidgetDataProvider>
          </WidgetChromeProvider>
        </DashboardFiltersProvider>
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { refetch };
}

const demoTile = (key: string, settings: Record<string, unknown> = {}): State => {
  const res = demoWidgetData(key, settings)!;
  return { status: "ok", data: res.data } as State;
};

beforeAll(async () => {
  await i18n.changeLanguage("de");
  registerCoreWidgets();
});
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("core.sla-gauge", () => {
  it("shows 87 % and 'Ziel 90 %' with an accessible label", () => {
    mount("core.sla-gauge", demoTile("core.sla-gauge", { target_percent: 90 }), { size: { w: 3, h: 3 } });
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("SLA-Erfüllung");
    expect(screen.getByRole("img", { name: "SLA-Erfüllung 87 %, Ziel 90 %" })).toBeInTheDocument();
    expect(screen.getByTestId("sla-gauge-svg")).toHaveTextContent("87 %");
    expect(screen.getByTestId("sla-gauge-target-label")).toHaveTextContent("Ziel 90 %");
    expect(screen.getByTestId("sla-gauge-tickets")).toHaveTextContent("412 Tickets im Zeitraum");
  });

  it("uses the target of the settings when the answer carries none, and 'Keine Tickets' for a null rate", () => {
    const base = demoTile("core.sla-gauge").data as Record<string, unknown>;
    mount("core.sla-gauge", { status: "ok", data: { ...base, rate: null, tickets: 0, target: undefined } } as State, {
      settings: { target_percent: 95 },
    });
    expect(screen.getByTestId("sla-gauge")).toHaveTextContent("Keine Tickets im Zeitraum");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("reads the rate from the job's own result (ratio -> percent) when the answer is deferred", () => {
    expect(
      gaugeFigures({
        columns: [
          { key: "sla_met_rate", role: "measure" },
          { key: "tickets", role: "measure" },
        ],
        rows: [[0.8765, 120]],
      } as never),
    ).toEqual({ rate: 87.7, tickets: 120 });
    expect(gaugeFigures({ columns: [], rows: [] } as never)).toEqual({ rate: null, tickets: 0 });
  });

  it("a deferred answer starts the background job like every report tile", async () => {
    const start = vi.spyOn(dashboardsApi, "startReportJob").mockResolvedValue({ job_id: 9, status: "queued" } as never);
    vi.spyOn(jobsApi, "get").mockResolvedValue({ id: 9, status: "running" } as unknown as Job);
    mount("core.sla-gauge", { status: "ok", data: { deferred: true, reason: "estimate", report: { key: null } } } as State);
    expect(await screen.findByText("Wird im Hintergrund berechnet …")).toBeInTheDocument();
    await waitFor(() => expect(start).toHaveBeenCalledWith(1, "w-1"));
  });

  it("draws the job's own result although the inline gauge has no report key (cache off)", async () => {
    vi.spyOn(dashboardsApi, "startReportJob").mockResolvedValue({ job_id: 9, status: "queued" } as never);
    const result = { columns: [{ key: "sla_met_rate", role: "measure" }, { key: "tickets", role: "measure" }], rows: [[0.875, 40]], totals: null, truncated: false };
    vi.spyOn(jobsApi, "get").mockResolvedValue({ id: 9, status: "succeeded", result } as unknown as Job);
    const get = vi.spyOn(reportsApi, "get");
    const definition = { source: "tickets", dimensions: [], measures: [{ key: "sla_met_rate" }, { key: "tickets" }], visualization: { type: "kpi" } };
    mount("core.sla-gauge", { status: "ok", data: { deferred: true, reason: "estimate", report: { key: null }, definition, row_cap: 1 } } as State, {
      settings: { target_percent: 90 },
    });
    expect(await screen.findByTestId("sla-gauge-svg", undefined, { timeout: 4000 })).toHaveTextContent("87,5 %");
    expect(get).not.toHaveBeenCalled();
  });
});

describe("core.backlog-aging", () => {
  it("renders the stacked bars with the age buckets as series", () => {
    mount("core.backlog-aging", demoTile("core.backlog-aging"));
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Rückstand nach Alter");
    expect(screen.getByTestId("backlog-aging-view-stacked")).toBeInTheDocument();
    const legend = screen.getByTestId("backlog-aging-view-stacked-legend");
    for (const label of demoReport("builtin.backlog_aging")!.definition.measures.map((m) => i18n.t(`reports.measures.${m.key}.label`, { defaultValue: "" }))) {
      if (label) expect(legend).toHaveTextContent(label);
    }
    expect(legend.querySelectorAll("li, span").length).toBeGreaterThanOrEqual(5);
  });

  it("caps the rows of the job's own result at max_rows", async () => {
    vi.spyOn(dashboardsApi, "startReportJob").mockResolvedValue({ job_id: 9, status: "queued" } as never);
    const tile = demoTile("core.backlog-aging").data as { result: { rows: unknown[] } };
    const full = { ...tile.result, rows: [...tile.result.rows, ...tile.result.rows, ...tile.result.rows] };
    vi.spyOn(jobsApi, "get").mockResolvedValue({ id: 9, status: "succeeded", result: full } as unknown as Job);
    vi.spyOn(reportsApi, "get").mockResolvedValue({ key: "builtin.backlog_aging", definition: demoReport("builtin.backlog_aging")!.definition } as ReportItem);
    mount("core.backlog-aging", { status: "ok", data: { deferred: true, report: { key: "builtin.backlog_aging" } } } as State, { settings: { max_rows: 2 } });
    expect(await screen.findByTestId("backlog-aging-view", undefined, { timeout: 4000 })).toBeInTheDocument();
    await act(async () => {});
    expect(screen.getByTestId("backlog-aging-view-stacked").querySelectorAll("[data-testid$='-hit-1']").length).toBe(1);
    expect(screen.queryByTestId("backlog-aging-view-stacked-hit-2")).toBeNull();
  });
});

describe("core.heatmap", () => {
  it("renders 7 weekdays x 24 hours, Monday first for German", () => {
    mount("core.heatmap", demoTile("core.heatmap"), { size: { w: 6, h: 4 } });
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Eingang nach Zeit");
    const heat = screen.getByTestId("heatmap-view-heatmap");
    expect(heat.querySelectorAll("rect[data-testid*='-cell-']")).toHaveLength(7 * 24);
    const labels = [...heat.querySelectorAll("svg > g > text > title")].map((n) => n.textContent);
    expect(labels).toEqual(["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]);
  });
});

describe.each([["core.sla-gauge"], ["core.backlog-aging"], ["core.heatmap"]])("%s without report rights", (key) => {
  it("shows the forbidden message without retry and is not usable for people without report rights", () => {
    mount(key, { status: "forbidden", error: "Für Berichte fehlt Ihnen die Berechtigung." } as State);
    expect(screen.getByText("Für Berichte fehlt Ihnen die Berechtigung.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Erneut versuchen" })).toBeNull();
    const def = DASHBOARD_WIDGETS.get(key)!;
    expect(def.category).toBe("reports");
    expect(canUseWidget(def, { is_admin: false, can_view_reports: false })).toBe(false);
    expect(canUseWidget(def, { is_admin: false, can_view_reports: true })).toBe(true);
  });
});
