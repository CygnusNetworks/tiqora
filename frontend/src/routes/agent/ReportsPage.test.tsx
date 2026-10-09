import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { DEMO_FACTS, DEMO_REPORT_CATALOGUE, demoSources, resolveReportRun } from "@/demo/reportsMock";
import { ApiError } from "@/lib/api";
import { dateRangeForPreset } from "@/lib/dateRanges";
import { reportsApi, type ReportRunIn } from "@/lib/reportsApi";
import { ReportsPage } from "./ReportsPage";

let searchParams: Record<string, unknown> = {};

vi.mock("@tanstack/react-router", () => ({
  useSearch: () => searchParams,
  useNavigate: () => vi.fn(),
  Link: ({
    children,
    to,
    params,
    search,
    className,
    ...rest
  }: {
    children: ReactNode;
    to: string;
    params?: Record<string, string>;
    search?: Record<string, unknown>;
    className?: string;
    [k: string]: unknown;
  }) => {
    let href = to.replace(/\$(\w+)/g, (_, name: string) => params?.[name] ?? "");
    const q = Object.entries(search ?? {}).filter(([, v]) => v !== undefined && v !== "");
    if (q.length) href += `?${q.map(([k, v]) => `${k}=${String(v)}`).join("&")}`;
    return (
      <a href={href} className={className} data-testid={rest["data-testid"] as string | undefined}>
        {children}
      </a>
    );
  },
}));

vi.mock("@/lib/reportsApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/reportsApi")>()),
  reportsApi: { catalogue: vi.fn(), run: vi.fn(), get: vi.fn(), export: vi.fn(), sources: vi.fn(), factsStatus: vi.fn() },
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

vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: { is_admin: false } }) }));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ReportsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const runCalls = () => vi.mocked(reportsApi.run).mock.calls.map((c) => c[0] as ReportRunIn);

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

beforeEach(() => {
  searchParams = {};
  vi.mocked(reportsApi.catalogue).mockReset().mockResolvedValue(DEMO_REPORT_CATALOGUE);
  vi.mocked(reportsApi.run)
    .mockReset()
    .mockImplementation(async (body) => resolveReportRun(body));
  vi.mocked(reportsApi.sources).mockReset().mockResolvedValue(demoSources());
  vi.mocked(reportsApi.factsStatus).mockReset().mockResolvedValue(DEMO_FACTS);
});

describe("ReportsPage", () => {
  it("shows the facts banner while data is being built", async () => {
    vi.mocked(reportsApi.catalogue).mockResolvedValue({
      ...DEMO_REPORT_CATALOGUE,
      facts: { ready: false, as_of: null, backfill_status: "running", backfill_job_id: 77, backfill_progress: 0.42 },
    });
    vi.mocked(reportsApi.factsStatus).mockResolvedValue({
      ready: false, as_of: null, backfill_status: "running", backfill_job_id: 77, backfill_progress: 0.42,
    });
    renderPage();
    const banner = await screen.findByTestId("reports-facts-banner");
    expect(banner).toHaveTextContent("Die Berichtsdaten werden aufgebaut (42 %)");
  });

  it("re-reads the build progress for agents and drops the banner once it is done", async () => {
    vi.mocked(reportsApi.catalogue).mockResolvedValue({
      ...DEMO_REPORT_CATALOGUE,
      facts: { ready: false, as_of: null, backfill_status: "running", backfill_job_id: 77, backfill_progress: 0.42 },
    });
    vi.mocked(reportsApi.factsStatus).mockResolvedValueOnce({
      ready: false, as_of: null, backfill_status: "running", backfill_job_id: 77, backfill_progress: 0.6,
    });
    renderPage();
    // the polled status replaces the catalogue's (stale) 42 %
    await waitFor(() => expect(screen.getByTestId("reports-facts-banner")).toHaveTextContent("(60 %)"));
    expect(reportsApi.factsStatus).toHaveBeenCalled();
  });

  it("hides the banner when the polled status says the data is ready", async () => {
    vi.mocked(reportsApi.catalogue)
      .mockResolvedValueOnce({
        ...DEMO_REPORT_CATALOGUE,
        facts: { ready: false, as_of: null, backfill_status: "running", backfill_job_id: 77, backfill_progress: 0.9 },
      })
      .mockResolvedValue(DEMO_REPORT_CATALOGUE);
    renderPage();
    await waitFor(() => expect(reportsApi.factsStatus).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByTestId("reports-facts-banner")).not.toBeInTheDocument());
    // finished: the catalogue is loaded again with complete numbers
    await waitFor(() => expect(vi.mocked(reportsApi.catalogue).mock.calls.length).toBeGreaterThan(1));
  });

  it("shows no facts banner once the data is ready", async () => {
    renderPage();
    await screen.findByTestId("reports-card-builtin.volume");
    expect(screen.queryByTestId("reports-facts-banner")).not.toBeInTheDocument();
  });

  it("runs the overview reports with the selected range and queue", async () => {
    renderPage();
    await screen.findByTestId("stats-preset-last7Days");
    fireEvent.click(screen.getByTestId("stats-preset-last7Days"));
    const range = dateRangeForPreset("last7Days");
    await waitFor(() =>
      expect(runCalls()).toContainEqual(
        expect.objectContaining({
          report_key: "builtin.ticket_kpis",
          compare_previous: true,
          time_range: { kind: "absolute", start: range.from, end: range.to },
        }),
      ),
    );
    expect(screen.getByTestId("stats-preset-last7Days")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByTestId("reports-filter-queues-add"));
    fireEvent.click(await screen.findByTestId("reports-filter-queues-add-menu-option-1"));
    await waitFor(() =>
      expect(runCalls()).toContainEqual(
        expect.objectContaining({
          report_key: "builtin.ticket_kpis",
          parameters: { queues: [1] },
          time_range: { kind: "absolute", start: range.from, end: range.to },
        }),
      ),
    );
    // the other overview reports follow the same filter
    for (const key of ["builtin.volume", "builtin.backlog_trend", "builtin.agent_activity"]) {
      expect(runCalls()).toContainEqual(
        expect.objectContaining({ report_key: key, parameters: { queues: [1] } }),
      );
    }
    // "open now" is a snapshot: never restricted by the date range
    const openNow = runCalls().filter((b) => b.report_key === "builtin.open_now");
    expect(openNow.length).toBeGreaterThan(0);
    expect(openNow.every((b) => b.time_range == null)).toBe(true);
  });

  it("shows KPI tiles, charts and the agent table", async () => {
    renderPage();
    expect(await screen.findByTestId("stats-kpis-kpi-tickets")).toBeInTheDocument();
    expect(await screen.findByTestId("stats-chart-volume-chart")).toBeInTheDocument();
    expect(await screen.findByTestId("stats-chart-backlog-chart")).toBeInTheDocument();
    expect(await screen.findByTestId("stats-chart-open-snapshot-chart")).toBeInTheDocument();
    const table = await screen.findByTestId("stats-workload-table");
    await waitFor(() => expect(table).toHaveTextContent("Anna Beispiel"));
    expect(within(table).getAllByRole("columnheader").map((c) => c.textContent)).toEqual([
      "Agent",
      "Offen",
      "Antworten",
      "Notizen",
      "Geschlossen",
    ]);
  });

  it("lists the catalogue grouped by category and links to the viewer", async () => {
    renderPage();
    const card = await screen.findByTestId("reports-card-builtin.sla_compliance");
    expect(card).toHaveAttribute("href", "/agent/reports/builtin.sla_compliance");
    expect(card).toHaveTextContent("SLA");
    const sla = screen.getByTestId("reports-category-sla");
    expect(within(sla).getByRole("heading")).toHaveTextContent("SLA");
    expect(within(sla).getByTestId("reports-card-builtin.sla_times")).toBeInTheDocument();
    expect(within(sla).queryByTestId("reports-card-builtin.volume")).not.toBeInTheDocument();
    // all 25 built-ins are listed
    expect(screen.getAllByTestId(/^reports-card-builtin\./)).toHaveLength(25);
  });

  it("highlights and scrolls to the category from ?category=", async () => {
    searchParams = { category: "time" };
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    renderPage();
    const group = await screen.findByTestId("reports-category-time");
    expect(group).toHaveAttribute("data-highlighted", "true");
    expect(screen.getByTestId("reports-category-sla")).not.toHaveAttribute("data-highlighted", "true");
    await waitFor(() => expect(scroll).toHaveBeenCalled());
    expect(scroll.mock.contexts).toContain(group);
    scroll.mockRestore();
  });

  it("open-now dimension tabs switch the dimension choice", async () => {
    renderPage();
    const section = await screen.findByTestId("stats-chart-open-snapshot");
    await waitFor(() => expect(section).toHaveTextContent("Support"));
    expect(screen.getByRole("tab", { name: "Queue" })).toHaveAttribute("aria-selected", "true");
    const stateRuns = () =>
      runCalls().filter((b) => b.report_key === "builtin.open_now" && b.dimension_choices?.["0"] === "state");
    // before any click nothing asks for the split by state, and the chart shows queues, not agents
    expect(stateRuns()).toHaveLength(0);
    expect(screen.getByTestId("stats-chart-open-snapshot-chart")).not.toHaveTextContent("Anna Beispiel");

    fireEvent.click(screen.getByRole("tab", { name: "Status" }));
    await waitFor(() => expect(stateRuns()).toHaveLength(1));
    expect(screen.getByRole("tab", { name: "Status" })).toHaveAttribute("aria-selected", "true");

    // owner: same request as the agent table (answered from the cache), so check the chart itself
    fireEvent.click(screen.getByRole("tab", { name: "Besitzer" }));
    expect(screen.getByRole("tab", { name: "Besitzer" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() =>
      expect(screen.getByTestId("stats-chart-open-snapshot-chart")).toHaveTextContent("Anna Beispiel"),
    );
    expect(screen.getByTestId("stats-chart-open-snapshot-chart")).not.toHaveTextContent("Support");
  });

  it("shows the server's message when a card's run is refused (429), not a spinner", async () => {
    const msg = "Es laufen schon drei Berichte für Sie. Bitte warten Sie, bis einer fertig ist.";
    vi.mocked(reportsApi.run).mockImplementation(async (body) => {
      if (body.report_key === "builtin.volume") {
        throw new ApiError(429, { detail: { message: msg } }, "/api/v1/reports/run");
      }
      return resolveReportRun(body);
    });
    renderPage();
    expect(await screen.findByTestId("stats-chart-volume-error")).toHaveTextContent(msg);
    expect(screen.queryByTestId("stats-chart-volume-loading")).not.toBeInTheDocument();
    // the other cards are unaffected
    expect(await screen.findByTestId("stats-chart-backlog-chart")).toBeInTheDocument();
  });

  it("opens a trend chart in the viewer with the overview's range, queues and buckets", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("stats-preset-last7Days"));
    const link = await screen.findByTestId("stats-chart-volume-open");
    expect(link.getAttribute("href")).toBe("/agent/reports/builtin.volume?preset=last7Days&grain=day");
    // "open now" has no date buckets
    expect(screen.getByTestId("stats-chart-open-snapshot-open").getAttribute("href")).toBe(
      "/agent/reports/builtin.open_now",
    );
  });

  it("does not run anything for a start date after the end date", async () => {
    renderPage();
    await screen.findByTestId("stats-chart-volume-chart");
    const before = runCalls().length;
    fireEvent.change(screen.getByTestId("reports-filter-from"), { target: { value: "2026-09-20" } });
    fireEvent.change(screen.getByTestId("reports-filter-to"), { target: { value: "2026-09-10" } });
    expect(await screen.findByTestId("reports-filter-range-error")).toHaveTextContent(
      "Das Startdatum liegt nach dem Enddatum",
    );
    const after = runCalls().slice(before);
    expect(after.some((b) => b.time_range?.kind === "absolute" && b.time_range.start === "2026-09-20" && b.time_range.end === "2026-09-10")).toBe(false);
  });

  it("formats the agent table numbers", async () => {
    vi.mocked(reportsApi.run).mockImplementation(async (body) => {
      const out = resolveReportRun(body);
      if (body.report_key === "builtin.agent_activity" && out.result) {
        out.result = { ...out.result, rows: [["2", "reply", 1234]], labels: { actor: { "2": "Anna Beispiel" } } };
      }
      return out;
    });
    renderPage();
    const row = await screen.findByTestId("stats-workload-row-2");
    await waitFor(() => expect(row).toHaveTextContent("1.234"));
  });
});
