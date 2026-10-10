import { describe, expect, it } from "vitest";
import { DEMO_REPORT_CATALOGUE } from "@/demo/reportsMock";
import type { ReportDefinition, ReportSource } from "@/lib/reportsApi";
import {
  allowedVisualizations,
  builderReducer,
  fromDefinition,
  initialState,
  toDefinition,
  validate,
  type BuilderAction,
  type BuilderState,
} from "./builderState";

const dim = (key: string, kind: string, ops: string[], extra: object = {}) => ({
  key,
  label_key: `reports.dimensions.${key}`,
  kind,
  labels: "none",
  i18n_values: null,
  filter_type: kind === "date" ? "datetime" : kind,
  options_source: null,
  time_field: kind === "date",
  ops,
  enum_values: [] as string[],
  ...extra,
});

const SRC = {
  key: "tickets",
  label_key: "reports.sources.tickets",
  default_time_field: "created",
  common_fields: {},
  dimensions: [
    dim("queue", "id", ["eq", "in"]),
    dim("owner", "id", ["eq", "in"]),
    dim("created", "date", ["before", "after"]),
    dim("channel", "enum", ["eq", "in"], { enum_values: ["email", "phone"] }),
  ],
  filter_fields: [],
  measures: [
    { key: "tickets", label_key: "reports.measures.tickets", agg: "sum", unit: "count" },
    { key: "closed_tickets", label_key: "reports.measures.closed_tickets", agg: "sum", unit: "count" },
  ],
} as ReportSource;

/** What the backend dump of a definition looks like: nulls and empty list defaults dropped, required defaults filled. */
function normalise(defn: ReportDefinition): unknown {
  const strip = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(strip);
    if (v && typeof v === "object") {
      const out: Record<string, unknown> = {};
      for (const [k, val] of Object.entries(v)) {
        if (val === null || val === undefined) continue;
        if (Array.isArray(val) && val.length === 0 && ["choices", "parameters", "chart_measures"].includes(k)) continue;
        out[k] = strip(val);
      }
      return out;
    }
    return v;
  };
  const full = {
    dimensions: [],
    sort: [],
    totals: { grand: false, rows: false, columns: false },
    ...defn,
    schema_version: 1,
    visualization: { type: "table", ...(defn.visualization ?? {}) },
  };
  return strip(full);
}

const keys = (s: BuilderState) => validate(s, SRC).map((i) => i.key);

describe("builderState", () => {
  it("starts empty and invalid", () => {
    const s = initialState();
    expect(toDefinition(s)).toBeNull();
    expect(validate(s, undefined).map((i) => i.key)).toEqual(["noSource", "noMeasure"]);
  });

  it("builds a definition with rows, a column as series and measures", () => {
    let s = initialState();
    for (const a of [
      { type: "setSource", source: "tickets" },
      { type: "addDimension", target: "rows", key: "created" },
      { type: "addDimension", target: "columns", key: "channel" },
      { type: "addMeasure", key: "tickets" },
      { type: "setVisualization", patch: { type: "stacked_bar" } },
    ] as BuilderAction[])
      s = builderReducer(s, a);
    expect(toDefinition(s)).toEqual({
      schema_version: 1,
      source: "tickets",
      dimensions: [{ key: "created", grain: "day" }, { key: "channel" }],
      measures: [{ key: "tickets" }],
      visualization: { type: "stacked_bar", series_dimension: 1 },
      sort: [],
      totals: { grand: false, rows: false, columns: false },
    });
  });

  it("round-trips every built-in definition of the demo catalogue", () => {
    expect(DEMO_REPORT_CATALOGUE.reports.length).toBeGreaterThan(20);
    for (const r of DEMO_REPORT_CATALOGUE.reports) {
      const back = toDefinition(fromDefinition(r.definition!));
      expect(back, r.key).toEqual(normalise(r.definition!));
    }
  });

  it("changing the source drops fields the new source lacks", () => {
    let s = fromDefinition({ source: "tickets", dimensions: [{ key: "channel" }], measures: [{ key: "tickets" }] } as never);
    s = builderReducer(s, { type: "setSource", source: "accounted_time" });
    expect(s.rows).toEqual([]);
    expect(s.measures).toEqual([]);
    expect(s.filter).toBeNull();
    expect(s.parameters).toEqual([]);
    expect(s.source).toBe("accounted_time");
  });

  it("limits dimensions to 3 in total and measures to 10", () => {
    let s = builderReducer(initialState(), { type: "setSource", source: "tickets" });
    for (const k of ["queue", "owner", "channel", "created"]) s = builderReducer(s, { type: "addDimension", target: "rows", key: k });
    expect(s.rows.length + s.columns.length).toBe(3);
    for (let i = 0; i < 12; i++) s = builderReducer(s, { type: "addMeasure", key: `m${i}` });
    expect(s.measures).toHaveLength(10);
  });

  it("allows only visualizations the shape supports", () => {
    let s = fromDefinition({
      source: "tickets",
      dimensions: [{ key: "queue" }],
      measures: [{ key: "tickets" }, { key: "closed_tickets" }],
    } as never);
    expect(allowedVisualizations(s, SRC)).toEqual(["table", "bar", "stacked_bar", "line", "area"]);
    s = builderReducer(s, { type: "removeMeasure", index: 1 });
    expect(allowedVisualizations(s, SRC)).toContain("donut");
    s = builderReducer(s, { type: "removeDimension", target: "rows", index: 0 });
    expect(allowedVisualizations(s, SRC)).toEqual(["table", "kpi"]);
  });

  it("an illegal visualization after an edit is reported, not silently changed", () => {
    let s = fromDefinition({
      source: "tickets",
      dimensions: [{ key: "queue" }, { key: "channel" }],
      measures: [{ key: "tickets" }],
      visualization: { type: "heatmap" },
    } as never);
    s = builderReducer(s, { type: "removeDimension", target: "rows", index: 1 });
    expect(s.visualization.type).toBe("heatmap");
    expect(validate(s, SRC).map((i) => i.key)).toContain("vizNeedsTwoDimensions");
    // R-VALIDATE: no definition the backend would render empty
    expect(toDefinition(s)).toBeNull();
  });

  it("moves dimensions and keeps the series index pointing at the column", () => {
    let s = fromDefinition({
      source: "tickets",
      dimensions: [{ key: "queue" }, { key: "owner" }, { key: "channel" }],
      measures: [{ key: "tickets" }],
      visualization: { type: "stacked_bar", series_dimension: 2 },
    } as never);
    expect(s.columns.map((c) => c.key)).toEqual(["channel"]);
    s = builderReducer(s, { type: "moveDimension", target: "rows", index: 1, delta: -1 });
    expect(toDefinition(s)?.dimensions?.map((d) => d.key)).toEqual(["owner", "queue", "channel"]);
    expect(toDefinition(s)?.visualization?.series_dimension).toBe(2);
  });

  it("parameters must name filterable fields and unique keys", () => {
    let s = fromDefinition({ source: "tickets", measures: [{ key: "tickets" }] } as never);
    s = builderReducer(s, {
      type: "setParameters",
      parameters: [
        { key: "queues", field: "queue", op: "in" },
        { key: "queues", field: "nope", op: "in" },
      ] as never,
    });
    expect(validate(s, SRC).map((i) => i.key)).toEqual(["duplicateParameter", "parameterFieldUnknown"]);
  });

  it("name and groups are checked only when the save dialog asks (R-VALIDATE)", () => {
    const s = fromDefinition({ source: "tickets", measures: [{ key: "tickets" }] } as never);
    expect(keys(s)).toEqual([]);
    expect(validate(s, SRC, { name: true, groupsMissing: true }).map((i) => i.key)).toEqual(["nameRequired", "groupsRequired"]);
    // save-only issues never block the definition
    expect(toDefinition(s)).not.toBeNull();
    expect(validate(builderReducer(s, { type: "setMeta", name: "Mein Bericht" }), SRC, { name: true }).map((i) => i.key)).toEqual([]);
  });

  it("reports an invalid filter condition via the shared filter model", () => {
    let s = fromDefinition({ source: "tickets", measures: [{ key: "tickets" }] } as never);
    s = builderReducer(s, { type: "setFilter", filter: { version: 1, where: { field: "queue", op: "in", value: [] } } as never });
    expect(keys(s)).toEqual(["filterInvalid"]);
    s = builderReducer(s, { type: "setFilter", filter: { version: 1, where: { field: "queue", op: "in", value: ["3"] } } as never });
    expect(keys(s)).toEqual([]);
  });

  it("keeps chart_measures on the same measures when measures move or are removed", () => {
    let s = fromDefinition({
      source: "tickets",
      dimensions: [{ key: "queue" }],
      measures: [{ key: "tickets" }, { key: "closed_tickets" }],
      visualization: { type: "bar", chart_measures: [1] },
    } as never);
    s = builderReducer(s, { type: "moveMeasure", index: 1, delta: -1 });
    expect(s.measures).toEqual(["closed_tickets", "tickets"]);
    expect(toDefinition(s)?.visualization?.chart_measures).toEqual([0]);
    s = builderReducer(s, { type: "removeMeasure", index: 0 });
    expect(toDefinition(s)?.visualization?.chart_measures).toBeUndefined();
  });

  const base = () => fromDefinition({ source: "tickets", dimensions: [{ key: "queue" }], measures: [{ key: "tickets" }, { key: "closed_tickets" }] } as never);
  const withParam = (param: object) =>
    builderReducer(base(), { type: "setParameters", parameters: [{ key: "queues", field: "queue", op: "in", ...param }] as never });

  it("prunes sort entries of removed measures and dimensions", () => {
    let s = builderReducer(base(), { type: "setSort", sort: [{ key: "closed_tickets", direction: "desc" }, { key: "queue", direction: "asc" }] });
    expect(keys(s)).toEqual([]);
    s = builderReducer(s, { type: "removeMeasure", index: 1 });
    expect(toDefinition(s)?.sort).toEqual([{ key: "queue", direction: "asc" }]);
    s = builderReducer(s, { type: "removeDimension", target: "rows", index: 0 });
    expect(toDefinition(s)?.sort).toEqual([]);
  });

  it("reports a sort key the backend would reject", () => {
    const s = builderReducer(base(), { type: "setSort", sort: [{ key: "nope", direction: "asc" }] });
    expect(keys(s)).toEqual(["sortInvalid"]);
    expect(toDefinition(s)).toBeNull();
  });

  it("parameter key must match the backend pattern", () => {
    expect(keys(withParam({ key: "Queue" }))).toEqual(["parameterKeyInvalid"]);
    expect(keys(withParam({ key: "q".repeat(41) }))).toEqual(["parameterKeyInvalid"]);
    expect(keys(withParam({ key: "queue_2" }))).toEqual([]);
  });

  it("parameter op must be allowed for the field", () => {
    expect(keys(withParam({ op: "is_true" }))).toEqual(["parameterOpInvalid"]);
    expect(keys(withParam({ op: "eq" }))).toEqual([]);
  });

  it("parameter values are typed like the backend checks them", () => {
    expect(keys(withParam({ default: ["3"] }))).toEqual(["parameterValueInvalid"]); // id field wants integers
    expect(keys(withParam({ allowed_values: [1, 2], default: [3] }))).toEqual(["parameterValueInvalid"]); // default not allowed
    expect(keys(withParam({ allowed_values: [1, 2], default: [2] }))).toEqual([]);
    const ch = builderReducer(base(), {
      type: "setParameters",
      parameters: [{ key: "c", field: "channel", op: "in", allowed_values: ["email", "fax"] }] as never,
    });
    expect(keys(ch)).toEqual(["parameterValueInvalid"]); // enum member
  });

  it("grain is dropped on non-date dimensions and cannot be set by a patch", () => {
    let s = builderReducer(base(), { type: "updateDimension", target: "rows", index: 0, patch: { grain: "week" } });
    expect(s.rows[0].grain).toBeNull();
    s = fromDefinition({ source: "tickets", dimensions: [{ key: "queue", grain: "week" }], measures: [{ key: "tickets" }] } as never);
    s = { ...s, rows: [{ ...s.rows[0], date: false }] };
    expect(toDefinition(s)?.dimensions?.[0]).toEqual({ key: "queue" });
    s = builderReducer(builderReducer(initialState(), { type: "setSource", source: "tickets" }), { type: "addDimension", target: "rows", key: "created" });
    s = builderReducer(s, { type: "updateDimension", target: "rows", index: 0, patch: { grain: "month" } });
    expect(s.rows[0].grain).toBe("month");
  });

  it("drops chart_measures outside the measures on load and patch", () => {
    let s = fromDefinition({ source: "tickets", measures: [{ key: "tickets" }], visualization: { type: "bar", chart_measures: [0, 4] } } as never);
    expect(s.visualization.chart_measures).toEqual([0]);
    s = builderReducer(s, { type: "setVisualization", patch: { chart_measures: [7] } });
    expect(s.visualization.chart_measures).toBeNull();
  });

  it("checks limit, top_n and the time range bounds", () => {
    let s = builderReducer(base(), { type: "setLimit", limit: 20000 });
    expect(keys(s)).toEqual(["limitInvalid"]);
    s = builderReducer(s, { type: "setLimit", limit: 50 });
    s = builderReducer(s, { type: "updateDimension", target: "rows", index: 0, patch: { topN: 101 } });
    expect(keys(s)).toEqual(["topNInvalid"]);
    s = builderReducer(s, { type: "updateDimension", target: "rows", index: 0, patch: { topN: 5 } });
    s = builderReducer(s, { type: "setTime", timeRange: { kind: "relative", unit: "day", count: 4000 } as never });
    expect(keys(s)).toEqual(["timeRangeInvalid"]);
    s = builderReducer(s, { type: "setTime", timeRange: { kind: "absolute", start: "2026-02-01", end: "2026-01-01" } as never });
    expect(keys(s)).toEqual(["timeRangeInvalid"]);
    s = builderReducer(s, { type: "setTime", timeRange: { kind: "relative", unit: "day", count: 30 } as never });
    expect(keys(s)).toEqual([]);
  });

  it("keeps the dimension order and series index exactly, even when the series is not last", () => {
    const defn = {
      schema_version: 1,
      source: "tickets",
      dimensions: [{ key: "channel" }, { key: "queue" }],
      measures: [{ key: "tickets" }],
      visualization: { type: "stacked_bar", series_dimension: 0 },
      sort: [],
      totals: { grand: false, rows: true, columns: false },
    } as ReportDefinition;
    const s = fromDefinition(defn);
    expect(s.columns.map((c) => c.key)).toEqual(["channel"]);
    expect(JSON.stringify(toDefinition(s))).toBe(JSON.stringify(defn));
  });

  it("remaps the series index when dimensions are removed or moved", () => {
    const three = fromDefinition({
      source: "tickets",
      dimensions: [{ key: "queue" }, { key: "channel" }, { key: "owner" }],
      measures: [{ key: "tickets" }],
      visualization: { type: "stacked_bar", series_dimension: 1 },
    } as never);
    let s = builderReducer(three, { type: "removeDimension", target: "rows", index: 0 });
    expect(toDefinition(s)?.dimensions?.map((d) => d.key)).toEqual(["channel", "owner"]);
    expect(toDefinition(s)?.visualization?.series_dimension).toBe(0);
    s = builderReducer(three, { type: "removeDimension", target: "rows", index: 1 }); // owner, after the column
    expect(toDefinition(s)?.dimensions?.map((d) => d.key)).toEqual(["queue", "channel"]);
    expect(toDefinition(s)?.visualization?.series_dimension).toBe(1);
    s = builderReducer(three, { type: "moveDimension", target: "columns", index: 0, delta: 1 });
    expect(toDefinition(s)?.dimensions?.map((d) => d.key)).toEqual(["queue", "owner", "channel"]);
    expect(toDefinition(s)?.visualization?.series_dimension).toBe(2);
    s = builderReducer(three, { type: "removeDimension", target: "columns", index: 0 });
    expect(toDefinition(s)?.visualization?.series_dimension).toBeUndefined();
    // adding a row keeps a last column last
    s = builderReducer(builderReducer(initialState(), { type: "addDimension", target: "columns", key: "channel" }), { type: "addDimension", target: "rows", key: "queue" });
    s = builderReducer(s, { type: "addMeasure", key: "tickets" });
    s = builderReducer(s, { type: "addDimension", target: "rows", key: "owner" });
    expect(toDefinition(s)?.dimensions?.map((d) => d.key)).toEqual(["queue", "owner", "channel"]);
    expect(toDefinition(s)?.visualization?.series_dimension).toBe(2);
  });
});
