import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { DEMO_FACTS, DEMO_REPORT_CATALOGUE, demoSources, resolveReportRun } from "@/demo/reportsMock";
import { ApiError } from "@/lib/api";
import { dateRangeForPreset } from "@/lib/dateRanges";
import { reportsApi, type ReportItem, type ReportRunIn } from "@/lib/reportsApi";
import { runRetry } from "@/components/reports/useReportRun";
import { ReportsPage } from "./ReportsPage";

let searchParams: Record<string, unknown> = {};
const navigate = vi.hoisted(() => vi.fn());

vi.mock("@tanstack/react-router", () => ({
  useSearch: () => searchParams,
  useNavigate: () => navigate,
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
  reportsApi: { catalogue: vi.fn(), run: vi.fn(), get: vi.fn(), export: vi.fn(), sources: vi.fn(), factsStatus: vi.fn(), remove: vi.fn() },
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

const authFlags = vi.hoisted(() => ({ canViewReports: true, canCreate: false, isAdmin: false }));
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 7, is_admin: authFlags.isAdmin, can_view_reports: authFlags.canViewReports, can_create_reports: authFlags.canCreate },
  }),
}));

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
  navigate.mockReset();
  authFlags.canCreate = false;
  authFlags.isAdmin = false;
  vi.mocked(reportsApi.catalogue).mockReset().mockResolvedValue(DEMO_REPORT_CATALOGUE);
  vi.mocked(reportsApi.run)
    .mockReset()
    .mockImplementation(async (body) => resolveReportRun(body));
  vi.mocked(reportsApi.sources).mockReset().mockResolvedValue(demoSources());
  vi.mocked(reportsApi.factsStatus).mockReset().mockResolvedValue(DEMO_FACTS);
});

describe("ReportsPage", () => {
  it("shows a notice and runs nothing for an agent without report access", async () => {
    authFlags.canViewReports = false;
    try {
      renderPage();
      expect(await screen.findByTestId("reports-denied")).toHaveTextContent("Kein Zugriff auf Berichte");
      expect(reportsApi.run).not.toHaveBeenCalled();
      expect(vi.mocked(reportsApi.catalogue)).not.toHaveBeenCalled();
    } finally {
      authFlags.canViewReports = true;
    }
  });

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

  describe("job cap (429)", () => {
    const msg = "Es laufen schon drei Berichte für Sie. Bitte warten Sie, bis einer fertig ist.";
    const cap = () => new ApiError(429, { detail: { message: msg } }, "/api/v1/reports/run");
    beforeEach(() => {
      runRetry.baseMs = 1;
    });
    afterEach(() => {
      runRetry.baseMs = 3000;
    });

    it("waits and retries a refused card, then shows the chart", async () => {
      let refused = 0;
      vi.mocked(reportsApi.run).mockImplementation(async (body) => {
        if (body.report_key === "builtin.volume" && refused < 2) {
          refused += 1;
          throw cap();
        }
        return resolveReportRun(body);
      });
      renderPage();
      expect(await screen.findByTestId("stats-chart-volume-chart")).toBeInTheDocument();
      expect(refused).toBe(2);
      expect(screen.queryByTestId("stats-chart-volume-error")).not.toBeInTheDocument();
    });

    it("shows the server's message with a retry button on the card and the key figures row", async () => {
      let open = false;
      vi.mocked(reportsApi.run).mockImplementation(async (body) => {
        if (!open && (body.report_key === "builtin.volume" || body.report_key === "builtin.ticket_kpis")) throw cap();
        return resolveReportRun(body);
      });
      renderPage();
      expect(await screen.findByTestId("stats-chart-volume-error", {}, { timeout: 3000 })).toHaveTextContent(msg);
      expect(await screen.findByTestId("stats-kpis-error")).toHaveTextContent(msg);
      expect(screen.queryByTestId("stats-chart-volume-loading")).not.toBeInTheDocument();
      // the other cards are unaffected
      expect(await screen.findByTestId("stats-chart-backlog-chart")).toBeInTheDocument();
      open = true;
      fireEvent.click(screen.getByTestId("stats-chart-volume-retry"));
      expect(await screen.findByTestId("stats-chart-volume-chart")).toBeInTheDocument();
      fireEvent.click(screen.getByTestId("stats-kpis-retry"));
      expect(await screen.findByTestId("stats-kpis-kpi-tickets")).toBeInTheDocument();
    });
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

  it("agent table: dashes for agents missing from a list, and the two 'Weitere' tails stay apart", async () => {
    vi.mocked(reportsApi.run).mockImplementation(async (body) => {
      const out = resolveReportRun(body);
      if (body.report_key === "builtin.open_now" && body.dimension_choices?.["0"] === "owner" && out.result) {
        out.result = {
          ...out.result,
          rows: [["2", 5], ["3", 4], ["__other__", 7]],
          labels: { owner: { "2": "Anna Beispiel", "3": "Bernd Ohnetaten" } },
        };
      }
      if (body.report_key === "builtin.agent_activity" && out.result) {
        out.result = {
          ...out.result,
          rows: [["2", "reply", 9], ["9", "note", 2], ["__other__", "reply", 11]],
          labels: { actor: { "2": "Anna Beispiel", "9": "Cora Nuraktiv" } },
        };
      }
      return out;
    });
    renderPage();
    const cells = async (key: string) => {
      const row = await screen.findByTestId(`stats-workload-row-${key}`);
      await waitFor(() => expect(row.querySelectorAll("td")).toHaveLength(4));
      return Array.from(row.querySelectorAll("td")).map((c) => c.textContent);
    };
    // open 5; in the activity list with 9 replies, no notes or closings
    await waitFor(async () => expect(await cells("2")).toEqual(["5", "9", "0", "0"]));
    // owns tickets but is outside the activity top list: not 0 replies, but unknown
    expect(await cells("3")).toEqual(["4", "—", "—", "—"]);
    // active but not among the owners
    expect(await cells("9")).toEqual(["—", "0", "2", "0"]);
    // each tail only fills its own report's columns
    expect(await cells("__other__:open")).toEqual(["7", "—", "—", "—"]);
    expect(await cells("__other__:activity")).toEqual(["—", "11", "0", "0"]);
  });

  describe("saved reports", () => {
    const definition = DEMO_REPORT_CATALOGUE.reports.find((r) => r.key === "builtin.volume")!.definition;
    const saved = (over: Partial<ReportItem> & { id: number; name: string }): ReportItem =>
      ({
        key: String(over.id),
        kind: "saved",
        definition,
        description: `Beschreibung ${over.name}`,
        visibility: "private",
        group_ids: [],
        owner: { user_id: 7, name: "Ich" },
        version: 1,
        overview: false,
        read_only: false,
        ...over,
      }) as ReportItem;
    const mockCatalogue = (extra: ReportItem[]) =>
      vi.mocked(reportsApi.catalogue).mockResolvedValue({
        ...DEMO_REPORT_CATALOGUE,
        reports: [...DEMO_REPORT_CATALOGUE.reports, ...extra],
      });
    const MINE = saved({ id: 41, name: "Meins" });
    const THEIRS = saved({
      id: 42,
      name: "Vom Team",
      owner: { user_id: 9, name: "Kim" },
      visibility: "groups",
      group_ids: [2],
      read_only: true,
    });

    it("lists my and shared saved reports above the catalogue", async () => {
      mockCatalogue([MINE, THEIRS]);
      authFlags.canCreate = true;
      renderPage();
      const mine = await screen.findByTestId("reports-saved-mine");
      expect(mine).toHaveTextContent("Meins");
      expect(mine).not.toHaveTextContent("Vom Team");
      expect(mine).toHaveTextContent("Nur ich");
      const shared = screen.getByTestId("reports-saved-shared");
      expect(shared).toHaveTextContent("Vom Team");
      expect(shared).toHaveTextContent("von Kim");
      expect(shared).toHaveTextContent("Geteilt mit: Gruppen");
      // before the built-in catalogue
      expect(mine.compareDocumentPosition(screen.getByTestId("reports-catalogue")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(screen.getByTestId("reports-saved-card-41-open")).toHaveAttribute("href", "/agent/reports/41");
      // saved reports are not mixed into the built-in categories
      expect(screen.queryByTestId("reports-card-41")).toBeNull();
    });

    it("offers a read-only shared report no edit or delete, but copy and download", async () => {
      mockCatalogue([THEIRS]);
      authFlags.canCreate = true;
      renderPage();
      fireEvent.click(await screen.findByTestId("reports-saved-card-42-menu"));
      expect(screen.getByTestId("reports-saved-card-42-copy")).toBeInTheDocument();
      expect(screen.getByTestId("reports-saved-card-42-json")).toBeInTheDocument();
      expect(screen.getByTestId("reports-saved-card-42-yaml")).toBeInTheDocument();
      expect(screen.queryByTestId("reports-saved-card-42-edit")).toBeNull();
      expect(screen.queryByTestId("reports-saved-card-42-delete")).toBeNull();
      fireEvent.click(screen.getByTestId("reports-saved-card-42-copy"));
      expect(navigate).toHaveBeenCalledWith({ to: "/agent/reports/new", search: { from: "42" } });
    });

    it("edits an own report", async () => {
      mockCatalogue([MINE]);
      authFlags.canCreate = true;
      renderPage();
      fireEvent.click(await screen.findByTestId("reports-saved-card-41-menu"));
      fireEvent.click(screen.getByTestId("reports-saved-card-41-edit"));
      expect(navigate).toHaveBeenCalledWith({ to: "/agent/reports/$reportKey/edit", params: { reportKey: "41" } });
    });

    it("deletes after confirmation and refreshes", async () => {
      const remove = vi.mocked(reportsApi.remove).mockReset().mockResolvedValue(undefined);
      mockCatalogue([MINE]);
      authFlags.canCreate = true;
      renderPage();
      fireEvent.click(await screen.findByTestId("reports-saved-card-41-menu"));
      fireEvent.click(screen.getByTestId("reports-saved-card-41-delete"));
      expect(await screen.findByTestId("confirm-dialog")).toHaveTextContent("„Meins“");
      expect(remove).not.toHaveBeenCalled(); // asks first
      fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
      await waitFor(() => expect(remove).toHaveBeenCalledWith(41));
      await waitFor(() => expect(vi.mocked(reportsApi.catalogue)).toHaveBeenCalledTimes(2));
    });

    it("keeps the report when deleting is cancelled", async () => {
      const remove = vi.mocked(reportsApi.remove).mockReset().mockResolvedValue(undefined);
      mockCatalogue([MINE]);
      authFlags.canCreate = true;
      renderPage();
      fireEvent.click(await screen.findByTestId("reports-saved-card-41-menu"));
      fireEvent.click(screen.getByTestId("reports-saved-card-41-delete"));
      fireEvent.click(await screen.findByTestId("confirm-dialog-cancel"));
      expect(remove).not.toHaveBeenCalled();
      expect(screen.getByTestId("reports-saved-card-41")).toBeInTheDocument();
    });

    it("lets the owner delete an own report without the save right", async () => {
      const remove = vi.mocked(reportsApi.remove).mockReset().mockResolvedValue(undefined);
      mockCatalogue([
        saved({ id: 44, name: "Altlast", read_only: true }),
        saved({ id: 45, name: "Altlast kaputt", read_only: true, definition: null, definition_error: "kaputt" }),
      ]);
      authFlags.canCreate = false;
      renderPage();
      fireEvent.click(await screen.findByTestId("reports-saved-card-45-menu"));
      expect(screen.getByTestId("reports-saved-card-45-delete")).toBeInTheDocument();
      fireEvent.click(screen.getByTestId("reports-saved-card-44-menu"));
      expect(screen.queryByTestId("reports-saved-card-44-edit")).toBeNull();
      fireEvent.click(screen.getByTestId("reports-saved-card-44-delete"));
      fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
      await waitFor(() => expect(remove).toHaveBeenCalledWith(44));
    });

    it("shows why a broken report cannot open and only lets it be deleted", async () => {
      mockCatalogue([
        saved({ id: 43, name: "Kaputt", definition: null, definition_error: "Die Datenbasis „plugin.x“ gibt es nicht mehr." }),
      ]);
      authFlags.canCreate = true;
      renderPage();
      const card = await screen.findByTestId("reports-saved-card-43");
      expect(within(card).getByTestId("reports-saved-card-43-error")).toHaveTextContent("Die Datenbasis „plugin.x“");
      expect(within(card).queryByTestId("reports-saved-card-43-open")).toBeNull();
      fireEvent.click(screen.getByTestId("reports-saved-card-43-menu"));
      expect(screen.getByTestId("reports-saved-card-43-delete")).toBeInTheDocument();
      expect(screen.queryByTestId("reports-saved-card-43-edit")).toBeNull();
      expect(screen.queryByTestId("reports-saved-card-43-copy")).toBeNull();
      expect(screen.queryByTestId("reports-saved-card-43-json")).toBeNull();
    });

    it("shows 'Neuer Bericht' and 'Kopieren' on built-ins only with the create right", async () => {
      renderPage();
      await screen.findByTestId("reports-card-builtin.volume");
      expect(screen.queryByTestId("reports-new")).toBeNull();
      expect(screen.queryByTestId("reports-card-builtin.volume-copy")).toBeNull();
    });

    it("with the create right: new report link and a copy button on built-in cards", async () => {
      authFlags.canCreate = true;
      renderPage();
      expect(await screen.findByTestId("reports-new")).toHaveAttribute("href", "/agent/reports/new");
      fireEvent.click(await screen.findByTestId("reports-card-builtin.volume-copy"));
      expect(navigate).toHaveBeenCalledWith({ to: "/agent/reports/new", search: { from: "builtin.volume" } });
    });

    it("admins can show everybody's reports", async () => {
      authFlags.isAdmin = true;
      renderPage();
      const toggle = await screen.findByTestId("reports-all-toggle");
      expect(vi.mocked(reportsApi.catalogue).mock.calls.at(-1)![1]).toEqual({ all: false });
      fireEvent.click(toggle);
      await waitFor(() => expect(vi.mocked(reportsApi.catalogue).mock.calls.at(-1)![1]).toEqual({ all: true }));
    });

    it("hides the admin toggle from agents", async () => {
      renderPage();
      await screen.findByTestId("reports-catalogue");
      expect(screen.queryByTestId("reports-all-toggle")).toBeNull();
    });
  });
});
