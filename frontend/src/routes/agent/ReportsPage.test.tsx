import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { DEMO_REPORT_CATALOGUE, resolveReportRun } from "@/demo/reportsMock";
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
  reportsApi: { catalogue: vi.fn(), run: vi.fn(), get: vi.fn(), export: vi.fn() },
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
});

describe("ReportsPage", () => {
  it("shows the facts banner while data is being built", async () => {
    vi.mocked(reportsApi.catalogue).mockResolvedValue({
      ...DEMO_REPORT_CATALOGUE,
      facts: { ready: false, as_of: null, backfill_status: "running", backfill_job_id: 77, backfill_progress: 0.42 },
    });
    renderPage();
    const banner = await screen.findByTestId("reports-facts-banner");
    expect(banner).toHaveTextContent("Die Berichtsdaten werden aufgebaut (42 %)");
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
      "Mitarbeiter",
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

    fireEvent.click(screen.getByRole("tab", { name: "Status" }));
    await waitFor(() =>
      expect(runCalls()).toContainEqual(
        expect.objectContaining({ report_key: "builtin.open_now", dimension_choices: { "0": "state" } }),
      ),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Bearbeiter" }));
    expect(screen.getByRole("tab", { name: "Bearbeiter" })).toHaveAttribute("aria-selected", "true");
    expect(runCalls()).toContainEqual(
      expect.objectContaining({ report_key: "builtin.open_now", dimension_choices: { "0": "owner" } }),
    );
    await waitFor(() => expect(screen.getByTestId("stats-chart-open-snapshot")).toHaveTextContent("Anna Beispiel"));
  });
});
