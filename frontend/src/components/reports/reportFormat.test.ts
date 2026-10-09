import { describe, it, expect } from "vitest";
import { createInstance } from "i18next";
import de from "@/i18n/locales/de.json";
import type { ReportResult } from "@/lib/reportsApi";
import { cellLabel, exportLabels, formatMeasure, isoWeekNumber, toSeries } from "./reportFormat";

const i18n = createInstance();
await i18n.init({
  lng: "de",
  resources: { de: { translation: de } },
  interpolation: { escapeValue: false },
});
const t = i18n.t.bind(i18n);

const result = {
  report_key: null,
  columns: [
    { key: "created", role: "dimension", type: "date", label_key: "reports.dimensions.created", grain: "week", i18n_values: null },
    { key: "channel", role: "dimension", type: "enum", label_key: "reports.dimensions.channel", grain: null, i18n_values: "reports.values.channel" },
    { key: "tickets", role: "measure", type: "count", label_key: "reports.measures.tickets", grain: null, i18n_values: null },
    { key: "closed_tickets", role: "measure", type: "count", label_key: "reports.measures.closed_tickets", grain: null, i18n_values: null },
  ],
  rows: [
    ["2024-06-03", "email", 5, 4],
    ["2024-06-03", "telegram", 2, 1],
    ["2024-06-10", "email", 3, 3],
  ],
  labels: {},
  totals: null,
  previous: null,
  row_count: 3,
  truncated: false,
  time_window: null,
  generated_at: "2024-06-12T00:00:00Z",
  cached: false,
  facts: { ready: true, as_of: null, backfill_status: "done", backfill_job_id: null, backfill_progress: 1 },
} as ReportResult;

describe("cellLabel", () => {
  it("labels enum values via i18n and the folded bucket via reports.other", () => {
    expect(cellLabel(result, "channel", "telegram", t, "de")).toBe("Telegram");
    expect(cellLabel(result, "channel", "email", t, "de")).toBe("E-Mail");
    expect(cellLabel(result, "channel", "__other__", t, "de")).toBe("Weitere");
    expect(cellLabel(result, "channel", null, t, "de")).toBe("—");
  });

  it("prefers server labels and falls back to the raw value", () => {
    const labelled = { ...result, labels: { channel: { telegram: "Chat" } } } as ReportResult;
    expect(cellLabel(labelled, "channel", "telegram", t, "de")).toBe("Chat");
    expect(cellLabel(result, "channel", "carrier-pigeon", t, "de")).toBe("carrier-pigeon");
  });

  it("formats week, month, hour and weekday buckets", () => {
    expect(cellLabel(result, "created", "2024-06-03", t, "de")).toBe("KW 23");
    const month = { ...result, columns: [{ ...result.columns[0], grain: "month" }] } as ReportResult;
    expect(cellLabel(month, "created", "2024-06-01", t, "de")).toBe("Juni 2024");
    const hour = { ...result, columns: [{ key: "h", role: "dimension", type: "hour", label_key: "x", grain: null, i18n_values: null }] } as ReportResult;
    expect(cellLabel(hour, "h", 9, t, "de")).toBe("09 Uhr");
    const wd = { ...result, columns: [{ key: "w", role: "dimension", type: "weekday", label_key: "x", grain: null, i18n_values: null }] } as ReportResult;
    expect(cellLabel(wd, "w", 3, t, "de")).toBe("Mittwoch");
  });

  it("computes ISO week numbers across the year boundary", () => {
    expect(isoWeekNumber("2024-12-30")).toBe(1);
    expect(isoWeekNumber("2021-01-01")).toBe(53);
    expect(isoWeekNumber("kaputt")).toBeNull();
  });
});

describe("formatMeasure", () => {
  it("formats measures", () => {
    expect(formatMeasure(0.125, "percent", "de")).toBe("12,5 %");
    expect(formatMeasure(45, "minutes", "de")).toBe("45 min");
    expect(formatMeasure(210, "minutes", "de")).toBe("3,5 h");
    expect(formatMeasure(null, "count", "de")).toBe("—");
  });

  it("never shows 60 min or 24 h for a smaller value", () => {
    expect(formatMeasure(59.6, "minutes", "de")).toBe("59,6 min");
    expect(formatMeasure(59.96, "minutes", "de")).toBe("1 h");
    expect(formatMeasure(60, "minutes", "de")).toBe("1 h");
    expect(formatMeasure(0.4, "minutes", "de")).toBe("0,4 min");
    expect(formatMeasure(1439.9, "minutes", "de")).toBe("1 T");
    expect(formatMeasure(23.5 * 60, "minutes", "de")).toBe("23,5 h");
    expect(formatMeasure(0.99, "hours", "de")).toBe("59,4 min");
  });

  it("formats days, hours, units and counts", () => {
    expect(formatMeasure(3000, "minutes", "de")).toBe("2,1 T");
    expect(formatMeasure(3000, "minutes", "en")).toBe("2.1 d");
    expect(formatMeasure(2, "hours", "de")).toBe("2 h");
    expect(formatMeasure(14.5, "units", "de")).toBe("14,50");
    expect(formatMeasure(1234, "count", "de")).toBe("1.234");
  });
});

describe("toSeries", () => {
  it("pivots rows into series by the series dimension", () => {
    const { categories, series } = toSeries(
      result,
      { type: "stacked_bar", series_dimension: 1, chart_measures: null },
      t,
    );
    expect(categories.map((c) => c.key)).toEqual(["2024-06-03", "2024-06-10"]);
    expect(categories.map((c) => c.label)).toEqual(["KW 23", "KW 24"]);
    expect(series.map((s) => [s.key, s.values])).toEqual([
      ["email", [5, 3]],
      ["telegram", [2, null]],
    ]);
    expect(series.map((s) => s.label)).toEqual(["E-Mail", "Telegram"]);
  });

  it("plots only the chosen measure when pivoting", () => {
    const { series } = toSeries(
      result,
      { type: "stacked_bar", series_dimension: 1, chart_measures: [1] },
      t,
    );
    expect(series.map((s) => [s.key, s.values])).toEqual([
      ["email", [4, 3]],
      ["telegram", [1, null]],
    ]);
  });

  it("one series per plotted measure without series dimension", () => {
    const flat = {
      ...result,
      columns: [result.columns[1], result.columns[2], result.columns[3]],
      rows: [["email", 5, 4], ["telegram", 2, 1]],
    } as ReportResult;
    const one = toSeries(flat, { type: "bar", series_dimension: null, chart_measures: [0] }, t);
    expect(one.categories.map((c) => c.label)).toEqual(["E-Mail", "Telegram"]);
    expect(one.series.map((s) => [s.key, s.label, s.values])).toEqual([["tickets", "Tickets", [5, 2]]]);
    const all = toSeries(flat, { type: "line", series_dimension: null, chart_measures: null }, t);
    expect(all.series.map((s) => s.key)).toEqual(["tickets", "closed_tickets"]);
  });
});

describe("exportLabels", () => {
  it("translates headers and enum values, leaves dates alone", () => {
    const out = exportLabels(result, t);
    expect(out.column_labels).toMatchObject({ channel: "Kanal", tickets: "Tickets" });
    expect(out.value_labels).toEqual({ channel: { email: "E-Mail", telegram: "Telegram" } });
    expect(out.totals_label).toBe("Summe");
  });
});
