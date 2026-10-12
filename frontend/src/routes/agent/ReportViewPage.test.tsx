import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { DEMO_FACTS, demoReport, demoSources, resolveReportRun } from "@/demo/reportsMock";
import { ApiError } from "@/lib/api";
import { jobsApi, type Job } from "@/lib/jobsApi";
import { reportsApi, type ReportExportIn, type ReportItem, type ReportParameter, type ReportRunIn } from "@/lib/reportsApi";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { ReportViewPage } from "./ReportViewPage";

let routeParams: Record<string, string> = { reportKey: "builtin.volume" };
let searchParams: Record<string, unknown> = {};
const navigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useParams: () => routeParams,
  useSearch: () => searchParams,
  useNavigate: () => navigate,
  Link: ({ children, to, className, search, ...rest }: { children: ReactNode; to: string; className?: string; search?: Record<string, string>; [k: string]: unknown }) => (
    <a href={search ? `${to}?${new URLSearchParams(search)}` : to} className={className} data-testid={rest["data-testid"] as string | undefined}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/reportsApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/reportsApi")>()),
  reportsApi: { catalogue: vi.fn(), run: vi.fn(), get: vi.fn(), export: vi.fn(), sources: vi.fn(), factsStatus: vi.fn() },
}));

vi.mock("@/lib/jobsApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/jobsApi")>()),
  jobsApi: { get: vi.fn(), cancel: vi.fn() },
}));

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  api: {
    listQueues: vi.fn(async () => [
      { id: 1, name: "Support", children: [] },
      { id: 2, name: "Vertrieb", children: [] },
    ]),
  },
}));

const authFlags = vi.hoisted(() => ({ canViewReports: true, canCreate: false }));
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: { is_admin: false, can_view_reports: authFlags.canViewReports, can_create_reports: authFlags.canCreate },
  }),
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ReportViewPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
  setDisplayTimeZone("Europe/Berlin");
});

beforeEach(() => {
  authFlags.canCreate = false;
  routeParams = { reportKey: "builtin.volume" };
  searchParams = {};
  navigate.mockReset();
  vi.mocked(reportsApi.get).mockReset().mockImplementation(async (key) => demoReport(key)!);
  vi.mocked(reportsApi.run)
    .mockReset()
    .mockImplementation(async (body) => resolveReportRun(body));
  vi.mocked(reportsApi.export).mockReset().mockResolvedValue({ job_id: 9201, status: "queued" });
  vi.mocked(reportsApi.sources).mockReset().mockResolvedValue(demoSources());
  vi.mocked(reportsApi.factsStatus).mockReset().mockResolvedValue(DEMO_FACTS);
  vi.mocked(jobsApi.get)
    .mockReset()
    .mockResolvedValue({
      id: 9201,
      kind: "core.report_export",
      status: "running",
      title_key: "jobs.kinds.core.report_export",
      progress_done: 0,
      progress_total: null,
      progress_message: null,
      cancel_requested: false,
      error: null,
      result: null,
      files: [],
      created_by: 1,
      created_at: "2026-10-08T09:00:00Z",
      started_at: null,
      finished_at: null,
    } as Job);
});

describe("ReportViewPage", () => {
  it("shows a notice and runs nothing for an agent without report access", async () => {
    authFlags.canViewReports = false;
    try {
      renderPage();
      expect(await screen.findByTestId("reports-denied")).toHaveTextContent("Kein Zugriff auf Berichte");
      expect(reportsApi.run).not.toHaveBeenCalled();
      expect(vi.mocked(reportsApi.get)).not.toHaveBeenCalled();
    } finally {
      authFlags.canViewReports = true;
    }
  });

  it("loads the definition, runs it and exports XLSX via a background job", async () => {
    searchParams = { preset: "custom", from: "2026-09-01", to: "2026-09-30", queues: "1,2" };
    renderPage();
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Erstellte und geschlossene Tickets");
    expect(screen.getByTestId("report-view-page")).toHaveTextContent("pro Woche erstellt");
    await screen.findByTestId("report-view-chart");
    expect(reportsApi.get).toHaveBeenCalledWith("builtin.volume", expect.anything());
    const body = vi.mocked(reportsApi.run).mock.calls.at(-1)![0] as ReportRunIn;
    expect(body).toMatchObject({
      report_key: "builtin.volume",
      time_range: { kind: "absolute", start: "2026-09-01", end: "2026-09-30" },
      time_zone: "Europe/Berlin",
      parameters: { queues: [1, 2] },
    });

    fireEvent.click(screen.getByTestId("report-export-xlsx"));
    await waitFor(() => expect(reportsApi.export).toHaveBeenCalledTimes(1));
    const exp = vi.mocked(reportsApi.export).mock.calls[0][0] as ReportExportIn;
    expect(exp).toMatchObject({
      report_key: "builtin.volume",
      format: "xlsx",
      title: "Erstellte und geschlossene Tickets",
      time_range: { kind: "absolute", start: "2026-09-01", end: "2026-09-30" },
      parameters: { queues: [1, 2] },
      column_labels: { occurred: "Zeitpunkt", kind: "Ereignis", events: "Ereignisse" },
      totals_label: "Summe",
    });
    expect(exp.info_lines).toEqual([
      "Zeitraum: 01.09.2026 – 30.09.2026",
      "Zeitzone: Europe/Berlin",
      "Queues: Support, Vertrieb",
    ]);
    expect(exp.value_labels?.kind).toMatchObject({ create: "Erstellt", close: "Geschlossen" });
    expect(await screen.findByTestId("job-dialog")).toBeInTheDocument();
  });

  it("exports CSV too", async () => {
    renderPage();
    await screen.findByTestId("report-view-chart");
    fireEvent.click(screen.getByTestId("report-export-csv"));
    await waitFor(() =>
      expect(reportsApi.export).toHaveBeenCalledWith(expect.objectContaining({ format: "csv" })),
    );
  });

  it("grain select only for reports with a date dimension", async () => {
    const first = renderPage();
    await screen.findByTestId("report-view-chart");
    expect(screen.getByTestId("report-filter-grain")).toHaveTextContent("Woche");
    fireEvent.click(screen.getByTestId("report-filter-grain"));
    fireEvent.click(await screen.findByTestId("report-filter-grain-panel-option-month"));
    expect(navigate).toHaveBeenCalledWith(
      expect.objectContaining({ search: expect.objectContaining({ grain: "month" }) }),
    );
    first.unmount();

    routeParams = { reportKey: "builtin.sla_compliance" };
    renderPage();
    await screen.findByTestId("report-view-table");
    expect(screen.getByTestId("report-filter-choice")).toBeInTheDocument();
    expect(screen.queryByTestId("report-filter-grain")).not.toBeInTheDocument();
  });

  it("passes grain and dimension choice from the address to the run", async () => {
    routeParams = { reportKey: "builtin.volume_by_queue" };
    searchParams = { choice: "priority" };
    renderPage();
    await screen.findByTestId("report-view-chart");
    expect(vi.mocked(reportsApi.run).mock.calls.at(-1)![0]).toMatchObject({
      report_key: "builtin.volume_by_queue",
      dimension_choices: { "0": "priority" },
    });
    expect(screen.queryByTestId("report-filter-grain")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Priorität" })).toHaveAttribute("aria-selected", "true");
  });

  it("hides the date range for a snapshot report", async () => {
    routeParams = { reportKey: "builtin.open_now" };
    renderPage();
    await screen.findByTestId("report-view-chart");
    expect(screen.queryByTestId("stats-preset-last7Days")).not.toBeInTheDocument();
    expect(vi.mocked(reportsApi.run).mock.calls.at(-1)![0].time_range).toBeUndefined();
  });

  it("shows the server message on 422/504", async () => {
    const msg = "Der Bericht hat zu lange gebraucht. Bitte den Zeitraum verkleinern.";
    vi.mocked(reportsApi.run).mockRejectedValue(new ApiError(504, { detail: { message: msg } }, "/api/v1/reports/run"));
    renderPage();
    expect(await screen.findByTestId("report-error")).toHaveTextContent(msg);
    expect(screen.queryByTestId("report-view")).not.toBeInTheDocument();
  });

  it("shows the field message of a 422", async () => {
    const msg = "„foo“ ist hier nicht wählbar.";
    vi.mocked(reportsApi.run).mockRejectedValue(
      new ApiError(422, { detail: { message: msg, path: "dimension_choices.0" } }, "/api/v1/reports/run"),
    );
    renderPage();
    expect(await screen.findByTestId("report-error")).toHaveTextContent(msg);
  });

  it("asks for the previous period for key figures, so the tiles show the change", async () => {
    routeParams = { reportKey: "builtin.ticket_kpis" };
    renderPage();
    await screen.findByTestId("report-view-kpis");
    expect(vi.mocked(reportsApi.run).mock.calls.at(-1)![0]).toMatchObject({
      report_key: "builtin.ticket_kpis",
      compare_previous: true,
    });
    expect(await screen.findByTestId("report-view-kpi-tickets-delta")).toBeInTheDocument();
  });

  it("does not ask for the previous period for other reports", async () => {
    renderPage();
    await screen.findByTestId("report-view-chart");
    expect(vi.mocked(reportsApi.run).mock.calls.at(-1)![0].compare_previous).toBe(false);
  });

  it("says what is wrong with a start after the end and runs nothing", async () => {
    searchParams = { preset: "custom", from: "2026-09-30", to: "2026-09-01" };
    renderPage();
    expect(await screen.findByTestId("report-filter-range-error")).toHaveTextContent(
      "Das Startdatum liegt nach dem Enddatum. Bitte den Zeitraum korrigieren.",
    );
    expect(screen.getByTestId("report-filter-from")).toHaveAttribute("aria-invalid", "true");
    expect(reportsApi.run).not.toHaveBeenCalled();
    expect(screen.queryByTestId("report-loading")).not.toBeInTheDocument();
    expect(screen.queryByTestId("report-export-xlsx")).not.toBeInTheDocument();
  });

  describe("saved reports", () => {
    const channel = (over: Partial<ReportParameter> = {}): ReportParameter => ({
      key: "channel",
      field: "channel",
      op: "in",
      required: false,
      ...over,
    });
    const saved = (parameters: ReportParameter[], over: Partial<ReportItem> = {}): ReportItem =>
      ({
        key: "41",
        kind: "saved",
        id: 41,
        name: "Meins",
        description: "Mein Bericht",
        definition: { ...demoReport("builtin.volume")!.definition, source: "tickets", parameters },
        visibility: "private",
        group_ids: [],
        owner: { user_id: 7, name: "Ich" },
        version: 1,
        overview: false,
        read_only: false,
        ...over,
      }) as ReportItem;
    const open = (item: ReportItem) => {
      routeParams = { reportKey: "41" };
      vi.mocked(reportsApi.get).mockResolvedValue(item);
      // the demo engine knows built-in keys only
      vi.mocked(reportsApi.run).mockImplementation(async (body) => resolveReportRun({ ...body, report_key: "builtin.volume" }));
    };
    const lastRun = () => vi.mocked(reportsApi.run).mock.calls.at(-1)![0] as ReportRunIn;

    it("runs a saved report by its id with parameter values from the address", async () => {
      open(saved([channel()]));
      searchParams = { params: { channel: ["email"] } };
      renderPage();
      await waitFor(() => expect(reportsApi.run).toHaveBeenCalled());
      expect(lastRun()).toMatchObject({ report_key: "41", parameters: { channel: ["email"] } });
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Meins");
      expect(screen.getByText("Mein Bericht")).toBeInTheDocument();
      expect(await screen.findByTestId("report-params-channel-chip-email")).toHaveTextContent("E-Mail");
    });

    it("puts a chosen value into the address", async () => {
      open(saved([channel()]));
      renderPage();
      fireEvent.click(await screen.findByTestId("report-params-channel"));
      fireEvent.click(screen.getByRole("option", { name: "Telefon" }));
      expect(navigate).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: expect.objectContaining({ params: { channel: ["phone"] } }) }),
      );
    });

    it("sends exactly one value for an eq parameter, even if the address carries more", async () => {
      open(saved([channel({ op: "eq" })]));
      searchParams = { params: { channel: ["email", "phone"] } };
      renderPage();
      await waitFor(() => expect(reportsApi.run).toHaveBeenCalled());
      expect(lastRun().parameters).toEqual({ channel: ["email"] });
    });

    it("sends a switched-on yes/no parameter", async () => {
      open(saved([{ key: "arch", field: "archived", op: "is_true", required: false }]));
      searchParams = { params: { arch: true } };
      renderPage();
      await waitFor(() => expect(reportsApi.run).toHaveBeenCalled());
      expect(lastRun().parameters).toEqual({ arch: true });
      expect(await screen.findByRole("switch")).toBeChecked();
    });

    it("waits for a required parameter and says which", async () => {
      open(saved([channel({ required: true })]));
      renderPage();
      expect(await screen.findByTestId("report-params-channel-required")).toHaveTextContent("Bitte Kanal wählen");
      expect(reportsApi.run).not.toHaveBeenCalled();
      expect(screen.queryByTestId("report-loading")).toBeNull();
    });

    it("runs once the required parameter has a value", async () => {
      open(saved([channel({ required: true })]));
      searchParams = { params: { channel: ["phone"] } };
      renderPage();
      await waitFor(() => expect(reportsApi.run).toHaveBeenCalled());
      expect(screen.queryByTestId("report-params-channel-required")).toBeNull();
      expect(lastRun().parameters).toEqual({ channel: ["phone"] });
    });

    it("leaves the queue parameter to the filter bar", async () => {
      open(saved([{ key: "queues", field: "queue", op: "in", required: false }]));
      renderPage();
      await screen.findByTestId("report-filter-queues");
      expect(screen.queryByTestId("report-params")).toBeNull();
    });

    it("shows edit, copy and download for a saved report", async () => {
      authFlags.canCreate = true;
      open(saved([]));
      renderPage();
      expect(await screen.findByTestId("report-view-edit")).toHaveAttribute("href", "/agent/reports/$reportKey/edit");
      expect(screen.getByTestId("report-view-copy")).toBeInTheDocument();
      fireEvent.click(screen.getByTestId("report-view-download"));
      expect(screen.getByTestId("report-view-download-json")).toBeInTheDocument();
      expect(screen.getByTestId("report-view-download-yaml")).toBeInTheDocument();
    });

    it("offers no edit on a read-only report and no edit/copy without the create right", async () => {
      authFlags.canCreate = true;
      open(saved([], { read_only: true }));
      const first = renderPage();
      await screen.findByTestId("report-view-copy");
      expect(screen.queryByTestId("report-view-edit")).toBeNull();
      first.unmount();
      authFlags.canCreate = false;
      open(saved([]));
      renderPage();
      await screen.findByTestId("report-view-download");
      expect(screen.queryByTestId("report-view-edit")).toBeNull();
      expect(screen.queryByTestId("report-view-copy")).toBeNull();
    });

    it("links 'Zeitplan anlegen' with the report key, only with the create right", async () => {
      authFlags.canCreate = true;
      const first = renderPage();
      expect(await screen.findByTestId("report-view-schedule")).toHaveAttribute(
        "href",
        "/agent/reports/schedules/new?report=builtin.volume",
      );
      expect(screen.getByTestId("report-view-schedule")).toHaveTextContent("Zeitplan anlegen");
      first.unmount();
      authFlags.canCreate = false;
      renderPage();
      await screen.findByTestId("report-view-chart");
      expect(screen.queryByTestId("report-view-schedule")).toBeNull();
    });

    it("has no saved-report actions on a built-in report", async () => {
      authFlags.canCreate = true;
      renderPage();
      await screen.findByTestId("report-view-chart");
      expect(screen.queryByTestId("report-view-edit")).toBeNull();
      expect(screen.queryByTestId("report-view-download")).toBeNull();
    });

    it("shows the deleted message on 410 and does not run", async () => {
      routeParams = { reportKey: "41" };
      vi.mocked(reportsApi.get).mockRejectedValue(
        new ApiError(410, { detail: { message: "Der Bericht „Meins“ wurde gelöscht." } }, "/api/v1/reports/41"),
      );
      renderPage();
      const gone = await screen.findByTestId("report-view-gone");
      expect(gone).toHaveTextContent("Dieser Bericht wurde gelöscht.");
      expect(screen.getByTestId("report-view-gone-back")).toHaveAttribute("href", "/agent/reports");
      expect(reportsApi.run).not.toHaveBeenCalled();
    });

    it("shows a not-found message on 404", async () => {
      routeParams = { reportKey: "999" };
      vi.mocked(reportsApi.get).mockRejectedValue(
        new ApiError(404, { detail: { message: "Bericht nicht gefunden." } }, "/api/v1/reports/999"),
      );
      renderPage();
      expect(await screen.findByTestId("report-view-notfound")).toHaveTextContent("Dieser Bericht wurde nicht gefunden.");
      expect(screen.queryByTestId("report-view-gone")).toBeNull();
    });

    it("shows why a broken report cannot open", async () => {
      open(saved([], { definition: null, definition_error: "Die Datenbasis „plugin.x“ gibt es nicht mehr." }));
      renderPage();
      expect(await screen.findByTestId("report-error")).toHaveTextContent("Die Datenbasis „plugin.x“");
      expect(reportsApi.run).not.toHaveBeenCalled();
    });
  });
});
