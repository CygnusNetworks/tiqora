import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { useState } from "react";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { dashboardsApi } from "@/lib/dashboardsApi";
import { jobsApi, type Job } from "@/lib/jobsApi";
import { reportsApi, type ReportItem } from "@/lib/reportsApi";
import { buildDemoResult, demoCatalogue, demoReport, demoRunOptions, demoSources } from "@/demo/reportsMock";
import { DASHBOARD_WIDGETS, type WidgetMode } from "@/plugins/dashboardWidgets";
import { canUseWidget, settingsWithDefaults } from "../definitions";
import { WidgetDataProvider, type WidgetDataValue } from "../data";
import { WidgetChromeProvider } from "../chrome/WidgetFrame";
import { DashboardFiltersProvider } from "../filters/filtersContext";
import { ReportSettingsEditor } from "../settings/editors/ReportSettings";
import { registerCoreWidgets } from "./index";
import type { ReportTileData } from "./ReportWidget";

const REPORT = demoReport("builtin.volume_by_queue")!;

function tile(over: Partial<ReportTileData> = {}): ReportTileData {
  const result = { ...buildDemoResult(REPORT, demoRunOptions(REPORT)), report_key: REPORT.key };
  return {
    report: { key: REPORT.key, kind: "builtin", title_key: REPORT.title_key },
    display: "auto",
    deferred: false,
    definition: REPORT.definition,
    result,
    ignored_filters: [],
    drillable_dimensions: [],
    ...over,
  };
}
const deferredTile = (): ReportTileData => ({
  report: { key: REPORT.key, kind: "builtin", title_key: REPORT.title_key },
  deferred: true,
  reason: "estimate",
});

function job(over: Partial<Job> = {}): Job {
  return {
    id: 7,
    kind: "report",
    status: "running",
    result: null,
    error: null,
    files: [],
    progress_done: 0,
    progress_total: null,
    progress_message: null,
    created_at: "2026-10-12T08:00:00Z",
    started_at: null,
    finished_at: null,
    created_by: 5,
    title_key: null,
    ...over,
  } as unknown as Job;
}

let client: QueryClient;
type Props = { state: Omit<WidgetDataValue, "refetch">; settings?: Record<string, unknown>; mode?: WidgetMode; dashboardId?: number; filters?: { queue_ids?: number[]; time_range?: null } };

function Harness({ state, settings = { report_key: REPORT.key }, mode = "view", dashboardId = 1, filters = {}, refetch }: Props & { refetch: () => void }) {
  const def = DASHBOARD_WIDGETS.get("core.report")!;
  const Component = def.component;
  return (
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <DashboardFiltersProvider filters={{ queue_ids: [], time_range: null, ...filters }}>
          <WidgetChromeProvider value={{ dashboardId }}>
            <WidgetDataProvider value={{ ...state, refetch }}>
              <Component instanceId="w-report" settings={settingsWithDefaults(def, settings, 1)} size={{ w: 6, h: 5 }} filters={{}} mode={mode} />
            </WidgetDataProvider>
          </WidgetChromeProvider>
        </DashboardFiltersProvider>
      </I18nextProvider>
    </QueryClientProvider>
  );
}

function mount(props: Props) {
  const refetch = vi.fn();
  const view = render(<Harness {...props} refetch={refetch} />);
  return { refetch, rerender: (next: Props) => view.rerender(<Harness {...next} refetch={refetch} />) };
}

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

describe("ReportWidget", () => {
  it("renders the report through ReportView in compact mode with the report's title", () => {
    mount({ state: { status: "ok", data: tile() } });
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(i18n.t(REPORT.title_key));
    expect(screen.getByTestId("report-widget-view")).toBeInTheDocument();
    expect(screen.queryByTestId("report-job-running")).toBeNull();
  });

  it("deferred result starts one background job per settings and filters, then refetches the widget on success", async () => {
    const start = vi.spyOn(dashboardsApi, "startReportJob").mockResolvedValue({ job_id: 7, status: "queued" } as never);
    const get = vi.spyOn(jobsApi, "get").mockResolvedValue(job({ status: "running" }));
    const { refetch, rerender } = mount({ state: { status: "ok", data: deferredTile() } });
    expect(await screen.findByText("Wird im Hintergrund berechnet …")).toBeInTheDocument();
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
    expect(start).toHaveBeenCalledWith(1, "w-report");
    // a new (equal) deferred answer for the same settings does not start another job
    rerender({ state: { status: "ok", data: deferredTile() } });
    expect(start).toHaveBeenCalledTimes(1);
    expect(refetch).not.toHaveBeenCalled();
    get.mockResolvedValue(job({ status: "succeeded" }));
    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(1), { timeout: 4000 });
    // other settings or filters: a new job
    rerender({ state: { status: "ok", data: deferredTile() }, settings: { report_key: REPORT.key, max_rows: 5 } });
    await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
    rerender({ state: { status: "ok", data: deferredTile() }, settings: { report_key: REPORT.key, max_rows: 5 }, filters: { queue_ids: [2] } });
    await waitFor(() => expect(start).toHaveBeenCalledTimes(3));
  });

  it("job cap 429 shows the message and 'Erneut versuchen'", async () => {
    const start = vi
      .spyOn(dashboardsApi, "startReportJob")
      .mockRejectedValueOnce(new ApiError(429, { detail: "zu viele" }, "/x"))
      .mockResolvedValue({ job_id: 8, status: "queued" } as never);
    vi.spyOn(jobsApi, "get").mockResolvedValue(job({ id: 8 }));
    mount({ state: { status: "ok", data: deferredTile() } });
    expect(await screen.findByText("Es laufen bereits mehrere Berichte im Hintergrund.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Wird im Hintergrund berechnet …")).toBeInTheDocument();
  });

  it("a failed job shows its error and 'Erneut versuchen'", async () => {
    vi.spyOn(dashboardsApi, "startReportJob").mockResolvedValue({ job_id: 7, status: "queued" } as never);
    vi.spyOn(jobsApi, "get").mockResolvedValue(job({ status: "failed", error: "Zeitüberschreitung" }));
    mount({ state: { status: "ok", data: deferredTile() } });
    expect(await screen.findByText("Zeitüberschreitung")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Erneut versuchen" })).toBeInTheDocument();
  });

  it("shows 'Der Queue-Filter gilt für diesen Bericht nicht.' for ignored filters", () => {
    mount({ state: { status: "ok", data: tile({ ignored_filters: ["queue"] }) } });
    expect(screen.getByText("Der Queue-Filter gilt für diesen Bericht nicht.")).toBeInTheDocument();
  });

  it("in preview mode a deferred report shows the 'zu umfangreich für die Vorschau' text and starts no job", async () => {
    const start = vi.spyOn(dashboardsApi, "startReportJob");
    mount({ state: { status: "ok", data: deferredTile() }, mode: "preview" });
    expect(screen.getByText(/Zu umfangreich für die Vorschau/)).toBeInTheDocument();
    await act(async () => {});
    expect(start).not.toHaveBeenCalled();
  });

  it("on a wallboard a deferred report says so and starts no job", async () => {
    const start = vi.spyOn(dashboardsApi, "startReportJob");
    mount({ state: { status: "ok", data: deferredTile() }, mode: "wallboard" });
    expect(screen.getByText("Dieser Bericht ist für das Wallboard zu umfangreich.")).toBeInTheDocument();
    await act(async () => {});
    expect(start).not.toHaveBeenCalled();
  });

  it("with the result cache off the job's own result is shown while the widget still answers deferred", async () => {
    vi.spyOn(dashboardsApi, "startReportJob").mockResolvedValue({ job_id: 7, status: "queued" } as never);
    const result = tile().result!;
    vi.spyOn(jobsApi, "get").mockResolvedValue(job({ status: "succeeded", result: result as never }));
    vi.spyOn(reportsApi, "get").mockResolvedValue({ ...REPORT, definition: REPORT.definition } as ReportItem);
    mount({ state: { status: "ok", data: deferredTile() } });
    expect(await screen.findByTestId("report-widget-view", undefined, { timeout: 4000 })).toBeInTheDocument();
  });

  it("is a 'reports' widget that only people who may view reports can use", () => {
    const def = DASHBOARD_WIDGETS.get("core.report")!;
    expect(def.category).toBe("reports");
    expect(canUseWidget(def, { is_admin: false, can_view_reports: false })).toBe(false);
    expect(canUseWidget(def, { is_admin: false, can_view_reports: true })).toBe(true);
  });
});

describe("ReportSettingsEditor", () => {
  const SAVED: ReportItem = {
    key: "42",
    kind: "saved",
    id: 42,
    name: "Kanäle im Quartal",
    read_only: false,
    overview: false,
    definition: {
      ...REPORT.definition,
      source: "tickets",
      parameters: [{ key: "ch", field: "channel", op: "in", required: false }],
    },
  } as ReportItem;

  function Editor({ initial, onValue }: { initial: Record<string, unknown>; onValue: (v: Record<string, unknown>) => void }) {
    const [value, setValue] = useState(initial);
    return (
      <QueryClientProvider client={client}>
        <I18nextProvider i18n={i18n}>
          <ReportSettingsEditor
            value={value}
            errors={{}}
            onChange={(n) => {
              setValue(n);
              onValue(n);
            }}
          />
        </I18nextProvider>
      </QueryClientProvider>
    );
  }

  it("lists built-in and saved reports and edits runtime parameters", async () => {
    const cat = demoCatalogue();
    vi.spyOn(reportsApi, "catalogue").mockResolvedValue({ ...cat, reports: [...cat.reports, SAVED] } as never);
    vi.spyOn(reportsApi, "sources").mockResolvedValue(demoSources());
    const seen = vi.fn();
    render(<Editor initial={{ report_key: "42", parameters: {}, dimension_choices: {}, display: "auto", max_rows: 10 }} onValue={seen} />);
    const select = (await screen.findByTestId("report-settings-report")) as HTMLSelectElement;
    await waitFor(() => expect(select.querySelectorAll("optgroup")).toHaveLength(2));
    const labels = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
    expect(labels).toContain("Kanäle im Quartal");
    expect(labels).toContain(i18n.t(REPORT.title_key));
    // the saved report has a runtime parameter: its control appears and edits `parameters`
    fireEvent.click(await screen.findByTestId("report-settings-param-ch"));
    fireEvent.click(await screen.findByRole("option", { name: "Telefon" }));
    expect(seen).toHaveBeenLastCalledWith(expect.objectContaining({ parameters: { ch: ["phone"] } }));
    // another report starts without the first one's parameters
    fireEvent.change(select, { target: { value: REPORT.key } });
    expect(seen).toHaveBeenLastCalledWith(expect.objectContaining({ report_key: REPORT.key, parameters: {} }));
  });

  it("the display, row count and own time range land in the settings", async () => {
    vi.spyOn(reportsApi, "catalogue").mockResolvedValue(demoCatalogue() as never);
    const seen = vi.fn();
    render(<Editor initial={{ report_key: REPORT.key, parameters: {}, dimension_choices: {}, display: "auto", max_rows: 10 }} onValue={seen} />);
    await screen.findByTestId("report-settings-report");
    fireEvent.change(screen.getByLabelText("Darstellung"), { target: { value: "table" } });
    expect(seen).toHaveBeenLastCalledWith(expect.objectContaining({ display: "table" }));
    fireEvent.change(screen.getByLabelText("Anzahl Zeilen"), { target: { value: "5" } });
    expect(seen).toHaveBeenLastCalledWith(expect.objectContaining({ max_rows: 5 }));
    fireEvent.change(screen.getByLabelText("Zeitraum"), { target: { value: "30d" } });
    expect(seen).toHaveBeenLastCalledWith(expect.objectContaining({ time_range: "30d" }));
    fireEvent.change(screen.getByLabelText("Zeitraum"), { target: { value: "" } });
    expect(seen.mock.lastCall![0]).not.toHaveProperty("time_range");
  });
});
