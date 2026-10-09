import { beforeAll, describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { demoReport } from "@/demo/reportsMock";
import type { ReportDefinition, ReportResult } from "@/lib/reportsApi";
import { ReportView } from "./ReportView";

function Providers({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
    </QueryClientProvider>
  );
}

const defOf = (key: string): ReportDefinition => demoReport(key)!.definition;
const volumeDef = defOf("builtin.volume");

function resultOf(over: Partial<ReportResult>): ReportResult {
  return {
    report_key: null,
    columns: [],
    rows: [],
    labels: {},
    totals: null,
    previous: null,
    row_count: over.rows?.length ?? 0,
    truncated: false,
    time_window: null,
    generated_at: "2024-06-20T08:00:00Z",
    cached: false,
    facts: { ready: true, as_of: null, backfill_status: "done", backfill_job_id: null, backfill_progress: 1 },
    ...over,
  };
}

// 2024-06-03 and 2024-06-10 are the Mondays of ISO weeks 23 and 24.
const volumeResult = resultOf({
  report_key: "builtin.volume",
  columns: [
    { key: "occurred", role: "dimension", type: "date", label_key: "reports.dimensions.occurred", grain: "week", i18n_values: null },
    { key: "kind", role: "dimension", type: "enum", label_key: "reports.dimensions.kind", grain: null, i18n_values: "reports.values.event_kind" },
    { key: "events", role: "measure", type: "count", label_key: "reports.measures.events", grain: null, i18n_values: null },
  ],
  rows: [
    ["2024-06-03", "create", 12],
    ["2024-06-03", "close", 9],
    ["2024-06-10", "create", 15],
    ["2024-06-10", "close", 11],
  ],
});

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

describe("ReportView", () => {
  it("renders the chart type from the definition and toggles to the table", async () => {
    render(
      <Providers>
        <ReportView report={{ key: "builtin.volume", definition: volumeDef }} result={volumeResult} />
      </Providers>,
    );
    expect(screen.getByTestId("report-view-chart")).toBeInTheDocument();
    // line chart of two series (Erstellt / Geschlossen) with a legend
    expect(screen.getByTestId("report-view-line-legend")).toHaveTextContent("Erstellt");
    expect(screen.getByTestId("report-view-line-legend")).toHaveTextContent("Geschlossen");
    expect(screen.queryByTestId("report-view-table")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("report-view-toggle-table"));
    expect(screen.getByTestId("report-view-table")).toHaveTextContent("KW 23");
    expect(screen.getByTestId("report-view-table")).toHaveTextContent("KW 24");
    expect(screen.queryByTestId("report-view-chart")).not.toBeInTheDocument();
    expect(screen.getByTestId("report-view-toggle-table")).toHaveTextContent("Diagramm anzeigen");
  });

  it("the table toggle is a real button reachable and usable by keyboard", () => {
    render(
      <Providers>
        <ReportView
          report={{ key: "builtin.created_heatmap", definition: defOf("builtin.created_heatmap") }}
          result={resultOf({
            columns: [
              { key: "created_weekday", role: "dimension", type: "weekday", label_key: "reports.dimensions.created_weekday", grain: null, i18n_values: null },
              { key: "created_hour", role: "dimension", type: "hour", label_key: "reports.dimensions.created_hour", grain: null, i18n_values: null },
              { key: "tickets", role: "measure", type: "count", label_key: "reports.measures.tickets", grain: null, i18n_values: null },
            ],
            rows: [
              ["1", "9", 4],
              ["1", "10", 6],
              ["2", "9", 3],
            ],
          })}
        />
      </Providers>,
    );
    expect(screen.getByTestId("report-view-heatmap")).toBeInTheDocument();
    const toggle = screen.getByTestId("report-view-toggle-table");
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle).toHaveAttribute("type", "button");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(toggle).not.toHaveAttribute("tabindex", "-1");
    // The dense heatmap cells are out of the tab order: the toggle is the first Tab stop.
    const root = screen.getByTestId("report-view");
    const tabbable = Array.from(
      root.querySelectorAll<HTMLElement>("button, a[href], input, select, [tabindex]"),
    ).filter((el) => el.tabIndex >= 0 && !el.hasAttribute("disabled"));
    expect(tabbable[0]).toBe(toggle);
    toggle.focus();
    expect(toggle).toHaveFocus();
    // A native button: Enter/Space activate it (jsdom only dispatches the click).
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(toggle).toHaveFocus();
    // Heatmap table view is the weekday x hour pivot.
    const table = screen.getByTestId("report-view-table");
    expect(within(table).getByTestId("report-view-pivot-row-1")).toHaveTextContent("Montag");
    expect(within(table).getByTestId("report-view-pivot-col-9")).toHaveTextContent("09 Uhr");
    fireEvent.click(toggle);
    expect(screen.getByTestId("report-view-heatmap")).toBeInTheDocument();
  });

  it("kpi visualization shows one tile per measure with previous-period delta", () => {
    const def = defOf("builtin.ticket_kpis");
    render(
      <Providers>
        <ReportView
          report={{ key: "builtin.ticket_kpis", definition: def }}
          result={resultOf({
            columns: def.measures.map((m) => ({
              key: m.key,
              role: "measure",
              type: m.key.endsWith("_rate") ? "percent" : m.key.includes("_p50") ? "minutes" : "count",
              label_key: `reports.measures.${m.key}`,
              grain: null,
              i18n_values: null,
            })),
            // tickets, closed_tickets, response_breach_rate, first_response_p50, solution_p50
            rows: [[120, 80, 0.1, 45, 600]],
            previous: { grand: [100, 80, 0.125, 30, null], rows: {}, columns: {} },
          })}
        />
      </Providers>,
    );
    const tiles = screen.getAllByTestId(/^report-view-kpi-[a-z0-9_]+$/);
    expect(tiles).toHaveLength(5);
    expect(screen.getByTestId("report-view-kpi-tickets")).toHaveTextContent("120");
    // more tickets: +20 %, neutral-good ("up" is good)
    const ticketsDelta = screen.getByTestId("report-view-kpi-tickets-delta");
    expect(ticketsDelta).toHaveTextContent("+20 %");
    expect(ticketsDelta.className).toContain("text-green");
    // fewer breaches is an improvement: -20 % in green
    const breachDelta = screen.getByTestId("report-view-kpi-response_breach_rate-delta");
    expect(breachDelta).toHaveTextContent("−20 %");
    expect(breachDelta.className).toContain("text-green");
    expect(screen.getByTestId("report-view-kpi-response_breach_rate")).toHaveTextContent("10 %");
    // slower first response is worse: +50 % in red
    const frDelta = screen.getByTestId("report-view-kpi-first_response_p50-delta");
    expect(frDelta).toHaveTextContent("+50 %");
    expect(frDelta.className).toContain("text-danger");
    // unchanged -> flat; no previous value -> no delta at all
    expect(screen.getByTestId("report-view-kpi-closed_tickets-delta").className).toContain("text-muted");
    expect(screen.queryByTestId("report-view-kpi-solution_p50-delta")).not.toBeInTheDocument();
    expect(screen.getByTestId("report-view-kpi-solution_p50")).toHaveTextContent("10 h");
    // KPI tiles are text already: no chart/table toggle
    expect(screen.queryByTestId("report-view-toggle-table")).not.toBeInTheDocument();
  });

  it("pivot shows row and column totals", () => {
    const def = defOf("builtin.agent_activity");
    render(
      <Providers>
        <ReportView
          report={{ key: "builtin.agent_activity", definition: def }}
          result={resultOf({
            columns: [
              { key: "actor", role: "dimension", type: "id", label_key: "reports.dimensions.actor", grain: null, i18n_values: null },
              { key: "kind", role: "dimension", type: "enum", label_key: "reports.dimensions.kind", grain: null, i18n_values: "reports.values.event_kind" },
              { key: "events", role: "measure", type: "count", label_key: "reports.measures.events", grain: null, i18n_values: null },
            ],
            rows: [
              ["2", "reply", 10],
              ["2", "close", 4],
              ["3", "reply", 6],
            ],
            labels: { actor: { "2": "Anna Beispiel", "3": "Ben Muster" } },
            // server row totals + grand; the column totals are summed client-side (count is additive)
            totals: { grand: [null, null, 20], rows: { "2": ["2", null, 14], "3": ["3", null, 6] }, columns: {} },
          })}
        />
      </Providers>,
    );
    expect(screen.getByTestId("report-view-pivot")).toBeInTheDocument();
    expect(screen.getByTestId("report-view-pivot-row-2")).toHaveTextContent("Anna Beispiel");
    expect(screen.getByTestId("report-view-pivot-rowtotal-2")).toHaveTextContent("14");
    expect(screen.getByTestId("report-view-pivot-rowtotal-3")).toHaveTextContent("6");
    expect(screen.getByTestId("report-view-pivot-coltotal-reply")).toHaveTextContent("16");
    expect(screen.getByTestId("report-view-pivot-coltotal-close")).toHaveTextContent("4");
    expect(screen.getByTestId("report-view-pivot-grand")).toHaveTextContent("20");
    // a missing combination is a dash, not 0
    expect(screen.getByTestId("report-view-pivot-cell-3-close")).toHaveTextContent("—");
    // pivot and table reports have no chart, so no toggle
    expect(screen.queryByTestId("report-view-toggle-table")).not.toBeInTheDocument();
  });

  it("shows a truncated hint", () => {
    render(
      <Providers>
        <ReportView
          report={{ key: "builtin.volume", definition: volumeDef }}
          result={{ ...volumeResult, truncated: true }}
        />
      </Providers>,
    );
    expect(screen.getByTestId("report-view-truncated")).toHaveTextContent("Die Tabelle ist gekürzt");
  });

  it("shows the empty text when there are no rows", () => {
    render(
      <Providers>
        <ReportView report={{ key: "builtin.volume", definition: volumeDef }} result={{ ...volumeResult, rows: [], row_count: 0 }} />
      </Providers>,
    );
    expect(screen.getByTestId("report-view-empty")).toHaveTextContent("Für diesen Zeitraum gibt es keine Daten.");
    expect(screen.queryByTestId("report-view-toggle-table")).not.toBeInTheDocument();
  });

  it("folds more than six series into Weitere and keeps enum colours fixed", () => {
    const def = defOf("builtin.volume_by_channel");
    const channels = ["email", "phone", "telegram", "webchat", "sms", "whatsapp", "internal"];
    render(
      <Providers>
        <ReportView
          report={{ key: "builtin.volume_by_channel", definition: def }}
          result={resultOf({
            columns: [
              { key: "created", role: "dimension", type: "date", label_key: "reports.dimensions.created", grain: "week", i18n_values: null },
              { key: "channel", role: "dimension", type: "enum", label_key: "reports.dimensions.channel", grain: null, i18n_values: "reports.values.channel" },
              { key: "tickets", role: "measure", type: "count", label_key: "reports.measures.tickets", grain: null, i18n_values: null },
            ],
            rows: channels.map((c, i) => ["2024-06-03", c, i + 1]),
          })}
        />
      </Providers>,
    );
    const legend = screen.getByTestId("report-view-stacked-legend");
    expect(within(legend).getAllByRole("listitem")).toHaveLength(7);
    expect(legend).toHaveTextContent("Weitere");
    // internal (7th in the fixed channel order) is folded: 7 = its own value
    expect(screen.getByTestId("report-view-stacked-seg-2024-06-03-__other__")).toHaveAttribute("fill", "var(--color-muted)");
    expect(screen.getByTestId("report-view-stacked-seg-2024-06-03-email")).toHaveAttribute("fill", "var(--color-series-1)");
    expect(screen.getByTestId("report-view-stacked-seg-2024-06-03-whatsapp")).toHaveAttribute("fill", "var(--color-series-6)");
  });
});
