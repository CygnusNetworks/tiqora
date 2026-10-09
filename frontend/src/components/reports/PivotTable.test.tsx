import { beforeAll, describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { ReportResult } from "@/lib/reportsApi";
import { PivotTable } from "./PivotTable";

const base: ReportResult = {
  report_key: "builtin.time_by_agent_month",
  columns: [
    { key: "agent", role: "dimension", type: "id", label_key: "reports.dimensions.agent", grain: null, i18n_values: null },
    { key: "booked", role: "dimension", type: "date", label_key: "reports.dimensions.booked", grain: "month", i18n_values: null },
    { key: "time_units", role: "measure", type: "units", label_key: "reports.measures.time_units", grain: null, i18n_values: null },
  ],
  // unsorted on purpose: date columns are shown in calendar order
  rows: [
    ["2", "2024-07-01", 3.5],
    ["2", "2024-06-01", 2],
    ["3", "2024-06-01", 1.25],
  ],
  labels: { agent: { "2": "Anna Beispiel", "3": "Ben Muster" } },
  totals: null,
  previous: null,
  row_count: 3,
  truncated: false,
  time_window: null,
  generated_at: "2024-08-01T00:00:00Z",
  cached: false,
  facts: { ready: true, as_of: null, backfill_status: "done", backfill_job_id: null, backfill_progress: 1 },
};

function renderPivot(result: ReportResult = base, props: { summable?: boolean; stock?: boolean } = { summable: true }) {
  return render(
    <I18nextProvider i18n={i18n}>
      <PivotTable result={result} testId="pv" {...props} />
    </I18nextProvider>,
  );
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

describe("PivotTable", () => {
  it("puts the second dimension in the header row and the first in the first column", () => {
    renderPivot();
    const headerCells = within(screen.getByTestId("pv-head")).getAllByRole("columnheader");
    // corner (first dimension name), June, July, total
    expect(headerCells.map((c) => c.textContent)).toEqual(["Agent", "Juni 2024", "Juli 2024", "Summe"]);
    expect(screen.getByTestId("pv-row-2")).toHaveTextContent("Anna Beispiel");
    expect(screen.getByTestId("pv-row-3")).toHaveTextContent("Ben Muster");
    expect(screen.getByTestId("pv-cell-2-2024-07-01")).toHaveTextContent("3,50");
    expect(screen.getByTestId("pv-cell-3-2024-07-01")).toHaveTextContent("—");
  });

  it("adds a totals column and a totals row (summed when the server sent none)", () => {
    renderPivot();
    expect(screen.getByTestId("pv-rowtotal-2")).toHaveTextContent("5,50");
    expect(screen.getByTestId("pv-rowtotal-3")).toHaveTextContent("1,25");
    expect(screen.getByTestId("pv-coltotal-2024-06-01")).toHaveTextContent("3,25");
    expect(screen.getByTestId("pv-coltotal-2024-07-01")).toHaveTextContent("3,50");
    expect(screen.getByTestId("pv-grand")).toHaveTextContent("6,75");
  });

  it("prefers the server totals (non-additive measures stay correct)", () => {
    renderPivot(
      {
      ...base,
      columns: [
        base.columns[0],
        base.columns[1],
        { key: "first_response_p50", role: "measure", type: "minutes", label_key: "reports.measures.first_response_p50", grain: null, i18n_values: null },
      ],
      rows: [
        ["2", "2024-06-01", 30],
        ["2", "2024-07-01", 50],
      ],
      totals: { grand: [null, null, 42], rows: { "2": ["2", null, 41] }, columns: { "2024-06-01": [null, "2024-06-01", 30] } },
      },
      { summable: false },
    );
    expect(screen.getByTestId("pv-rowtotal-2")).toHaveTextContent("41 min");
    expect(screen.getByTestId("pv-coltotal-2024-06-01")).toHaveTextContent("30 min");
    // a median is not summed: no server value -> dash
    expect(screen.getByTestId("pv-coltotal-2024-07-01")).toHaveTextContent("—");
    expect(screen.getByTestId("pv-grand")).toHaveTextContent("42 min");
  });

  it("keeps the header row and the first column in view while scrolling", () => {
    renderPivot();
    expect(screen.getByTestId("pv-head").className).toContain("sticky");
    expect(screen.getByTestId("pv-head").className).toContain("top-0");
    expect(screen.getByTestId("pv-row-2").className).toContain("sticky");
    expect(screen.getByTestId("pv-row-2").className).toContain("left-0");
    expect(screen.getByTestId("pv-row-2").tagName).toBe("TH");
    expect(screen.getByTestId("pv-row-2")).toHaveAttribute("scope", "row");
  });

  it("never sums a non-additive measure (distinct counts, rates): no totals without the server", () => {
    renderPivot(base, { summable: false });
    expect(screen.queryByTestId("pv-rowtotal-2")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pv-coltotal-2024-06-01")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pv-grand")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("pv-head")).queryByText("Summe")).not.toBeInTheDocument();
  });

  it("adds a stock up within one day but never over days", () => {
    renderPivot(
      {
        ...base,
        columns: [
          { key: "snapshot", role: "dimension", type: "date", label_key: "reports.dimensions.snapshot", grain: "day", i18n_values: null },
          { key: "state_type", role: "dimension", type: "enum", label_key: "reports.dimensions.state_type", grain: null, i18n_values: "reports.values.state_type" },
          { key: "open_tickets", role: "measure", type: "count", label_key: "reports.measures.open_tickets", grain: null, i18n_values: null },
        ],
        rows: [
          ["2024-06-01", "new", 4],
          ["2024-06-01", "open", 10],
          ["2024-06-02", "new", 3],
          ["2024-06-02", "open", 12],
        ],
        labels: {},
      },
      { summable: true, stock: true },
    );
    // per day across the states: the day's open tickets
    expect(screen.getByTestId("pv-rowtotal-2024-06-01")).toHaveTextContent("14");
    expect(screen.getByTestId("pv-rowtotal-2024-06-02")).toHaveTextContent("15");
    // per state across days would count the same tickets again: no totals row
    expect(screen.queryByTestId("pv-coltotal-new")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pv-grand")).not.toBeInTheDocument();
  });
});
