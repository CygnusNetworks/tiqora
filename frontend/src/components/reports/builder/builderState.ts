/**
 * State of the report builder: a pure reducer plus the mapping to and from the
 * wire `ReportDefinition`. No React, no I/O.
 *
 * The state keeps "rows" (dimensions in order) and "columns" (at most one: the
 * series dimension). `toDefinition` emits rows first, then the column, and points
 * `visualization.series_dimension` at the column.
 */
import type { TFunction } from "i18next";
import type { Schemas } from "@tiqora/api-client";
import { fieldsFromSource } from "@/components/filters/filterFields";
import { ticketFilterErrors } from "@/components/filters/filterModel";
import type { Grain, ReportDefinition, ReportSource } from "@/lib/reportsApi";
import type { TicketFilter } from "@/lib/savedSearchesApi";

export type ReportParameter = Schemas["ReportParameter"];
export type Visualization = NonNullable<ReportDefinition["visualization"]>;
export type VisualizationType = NonNullable<Visualization["type"]>;
type DimensionRef = ReportDefinition["dimensions"] extends (infer D)[] | undefined ? D : never;

export const MAX_DIMENSIONS = 3;
export const MAX_MEASURES = 10;
export const MAX_PARAMETERS = 10;

/** `date` marks a time dimension: only those carry a grain (backend: grain on other kinds is a 422). */
export type DimensionSlot = { key: string; grain: Grain | null; choices: string[]; topN: number | null; date?: boolean };

export type BuilderState = {
  name: string;
  description: string;
  source: string | null;
  rows: DimensionSlot[];
  columns: DimensionSlot[];
  /** Position of the column in the full dimension list (= number of rows before it); null = last. Keeps a loaded order exactly. */
  columnAt: number | null;
  measures: string[];
  filter: TicketFilter | null;
  parameters: ReportParameter[];
  timeField: string | null;
  timeRange: ReportDefinition["time_range"] | null;
  timeZone: string | null;
  visualization: Visualization;
  sort: NonNullable<ReportDefinition["sort"]>;
  limit: number | null;
  totals: NonNullable<ReportDefinition["totals"]>;
};

export type BuilderAction =
  | { type: "setSource"; source: string }
  | { type: "addDimension"; target: "rows" | "columns"; key: string; date?: boolean }
  | { type: "removeDimension"; target: "rows" | "columns"; index: number }
  | { type: "moveDimension"; target: "rows" | "columns"; index: number; delta: -1 | 1 }
  | { type: "updateDimension"; target: "rows" | "columns"; index: number; patch: Partial<DimensionSlot> }
  | { type: "addMeasure"; key: string }
  | { type: "removeMeasure"; index: number }
  | { type: "moveMeasure"; index: number; delta: -1 | 1 }
  | { type: "setFilter"; filter: TicketFilter | null }
  | { type: "setTime"; timeField?: string | null; timeRange?: BuilderState["timeRange"]; timeZone?: string | null }
  | { type: "setVisualization"; patch: Partial<Visualization> }
  | { type: "setSort"; sort: BuilderState["sort"] }
  | { type: "setLimit"; limit: number | null }
  | { type: "setTotals"; patch: Partial<BuilderState["totals"]> }
  | { type: "setParameters"; parameters: ReportParameter[] }
  | { type: "setMeta"; name?: string; description?: string }
  | { type: "load"; state: BuilderState };

export type BuilderIssue = { path: string; key: string };

/** Date dimensions of the built-in sources; the UI passes `date` from the source when it knows better. */
const KNOWN_DATE_DIMENSIONS: ReadonlySet<string> = new Set(["created", "closed", "first_response", "occurred", "booked"]);

export function initialState(): BuilderState {
  return {
    name: "",
    description: "",
    source: null,
    rows: [],
    columns: [],
    columnAt: null,
    measures: [],
    filter: null,
    parameters: [],
    timeField: null,
    timeRange: null,
    timeZone: null,
    visualization: { type: "table" },
    sort: [],
    limit: null,
    totals: { grand: false, rows: false, columns: false },
  };
}

function swap<T>(list: T[], index: number, delta: -1 | 1): T[] {
  const to = index + delta;
  if (index < 0 || index >= list.length || to < 0 || to >= list.length) return list;
  const next = [...list];
  [next[index], next[to]] = [next[to], next[index]];
  return next;
}

/** All dimensions in definition order: the column sits at `columnAt`, else last. */
function orderedSlots(s: BuilderState): DimensionSlot[] {
  if (s.columns.length === 0) return [...s.rows];
  const at = Math.min(s.columnAt ?? s.rows.length, s.rows.length);
  return [...s.rows.slice(0, at), s.columns[0], ...s.rows.slice(at)];
}

function dimensionCount(s: BuilderState): number {
  return s.rows.length + s.columns.length;
}

/** `chart_measures` holds measure indexes: keep them pointing at the same measures. */
function remapChartMeasures(viz: Visualization, map: (i: number) => number | null): Visualization {
  const cm = viz.chart_measures;
  if (!cm) return viz;
  const next = cm.map(map).filter((i): i is number => i !== null);
  return { ...viz, chart_measures: next.length > 0 ? next : null };
}

/** Drops `chart_measures` indexes that point outside the measures (backend: 422). */
function clampChartMeasures(viz: Visualization, count: number): Visualization {
  if (!viz.chart_measures) return viz;
  const kept = viz.chart_measures.filter((i) => Number.isInteger(i) && i >= 0 && i < count);
  return { ...viz, chart_measures: kept.length > 0 ? kept : null };
}

export function builderReducer(state: BuilderState, action: BuilderAction): BuilderState {
  switch (action.type) {
    case "setSource": {
      if (state.source === action.source) return state;
      return { ...initialState(), name: state.name, description: state.description, source: action.source };
    }
    case "addDimension": {
      if (dimensionCount(state) >= MAX_DIMENSIONS) return state;
      if ([...state.rows, ...state.columns].some((d) => d.key === action.key)) return state;
      if (action.target === "columns" && state.columns.length >= 1) return state;
      const isDate = action.date ?? KNOWN_DATE_DIMENSIONS.has(action.key);
      const slot: DimensionSlot = { key: action.key, grain: isDate ? "day" : null, choices: [], topN: null, date: isDate };
      if (action.target === "columns") return { ...state, columns: [slot], columnAt: state.rows.length };
      // A column that was last stays last when rows are added.
      const columnLast = state.columns.length > 0 && (state.columnAt ?? state.rows.length) >= state.rows.length;
      return {
        ...state,
        rows: [...state.rows, slot],
        columnAt: columnLast ? state.rows.length + 1 : state.columnAt,
      };
    }
    case "removeDimension": {
      const list = state[action.target];
      if (action.index < 0 || action.index >= list.length) return state;
      const removed = list[action.index].key;
      let columnAt = state.columnAt;
      if (action.target === "columns") columnAt = null;
      else if (columnAt !== null && action.index < columnAt) columnAt -= 1;
      return {
        ...state,
        [action.target]: list.filter((_, i) => i !== action.index),
        columnAt,
        sort: state.sort.filter((s) => s.key !== removed),
      };
    }
    case "moveDimension":
      if (action.target === "columns") {
        if (state.columns.length === 0) return state;
        const at = (state.columnAt ?? state.rows.length) + action.delta;
        return { ...state, columnAt: Math.max(0, Math.min(state.rows.length, at)) };
      }
      return { ...state, [action.target]: swap(state[action.target], action.index, action.delta) };
    case "updateDimension": {
      const list = state[action.target];
      if (action.index < 0 || action.index >= list.length) return state;
      return {
        ...state,
        [action.target]: list.map((d, i) => {
          if (i !== action.index) return d;
          const next = { ...d, ...action.patch };
          // A grain only makes sense on a time dimension.
          if (!d.date && !KNOWN_DATE_DIMENSIONS.has(d.key)) next.grain = null;
          return next;
        }),
      };
    }
    case "addMeasure":
      if (state.measures.length >= MAX_MEASURES || state.measures.includes(action.key)) return state;
      return { ...state, measures: [...state.measures, action.key] };
    case "removeMeasure": {
      if (action.index < 0 || action.index >= state.measures.length) return state;
      return {
        ...state,
        measures: state.measures.filter((_, i) => i !== action.index),
        sort: state.sort.filter((s) => s.key !== state.measures[action.index]),
        visualization: remapChartMeasures(state.visualization, (i) => (i === action.index ? null : i > action.index ? i - 1 : i)),
      };
    }
    case "moveMeasure": {
      const measures = swap(state.measures, action.index, action.delta);
      if (measures === state.measures) return state;
      const to = action.index + action.delta;
      return {
        ...state,
        measures,
        visualization: remapChartMeasures(state.visualization, (i) => (i === action.index ? to : i === to ? action.index : i)),
      };
    }
    case "setFilter":
      return { ...state, filter: action.filter };
    case "setTime":
      return {
        ...state,
        timeField: action.timeField !== undefined ? action.timeField : state.timeField,
        timeRange: action.timeRange !== undefined ? action.timeRange : state.timeRange,
        timeZone: action.timeZone !== undefined ? action.timeZone : state.timeZone,
      };
    case "setVisualization":
      // The series dimension is derived from the columns, never set directly.
      return {
        ...state,
        visualization: clampChartMeasures(
          { ...state.visualization, ...action.patch, series_dimension: null },
          state.measures.length,
        ),
      };
    case "setSort":
      return { ...state, sort: action.sort };
    case "setLimit":
      return { ...state, limit: action.limit };
    case "setTotals":
      return { ...state, totals: { ...state.totals, ...action.patch } };
    case "setParameters":
      return { ...state, parameters: action.parameters.slice(0, MAX_PARAMETERS) };
    case "setMeta":
      return {
        ...state,
        name: action.name ?? state.name,
        description: action.description ?? state.description,
      };
    case "load":
      return action.state;
  }
}

// --- definition mapping -----------------------------------------------------

function slotFromRef(d: DimensionRef): DimensionSlot {
  return {
    key: d.key,
    grain: (d.grain ?? null) as Grain | null,
    date: d.grain != null || KNOWN_DATE_DIMENSIONS.has(d.key),
    choices: d.choices ? [...d.choices] : [],
    topN: d.top_n ?? null,
  };
}

export function fromDefinition(defn: ReportDefinition, meta?: { name?: string; description?: string }): BuilderState {
  const dims = defn.dimensions ?? [];
  const seriesIdx = defn.visualization?.series_dimension ?? null;
  const hasColumn = seriesIdx !== null && seriesIdx >= 0 && seriesIdx < dims.length;
  const rows = dims.filter((_, i) => !hasColumn || i !== seriesIdx).map(slotFromRef);
  // Position of the column among all dimensions is kept exactly as stored.
  const columns = hasColumn ? [slotFromRef(dims[seriesIdx])] : [];
  const base = initialState();
  const viz = defn.visualization;
  return {
    ...base,
    name: meta?.name ?? "",
    description: meta?.description ?? "",
    source: defn.source,
    rows,
    columns,
    columnAt: hasColumn ? seriesIdx : null,
    measures: (defn.measures ?? []).map((m) => m.key),
    filter: defn.filter ?? null,
    parameters: defn.parameters ? [...defn.parameters] : [],
    timeField: defn.time_field ?? null,
    timeRange: defn.time_range ?? null,
    timeZone: defn.time_zone ?? null,
    visualization: clampChartMeasures(
      {
        type: viz?.type ?? "table",
        series_dimension: null,
        ...(viz?.chart_measures && viz.chart_measures.length > 0 ? { chart_measures: [...viz.chart_measures] } : {}),
      },
      (defn.measures ?? []).length,
    ),
    sort: defn.sort ? [...defn.sort] : [],
    limit: defn.limit ?? null,
    totals: { grand: false, rows: false, columns: false, ...(defn.totals ?? {}) },
  };
}

function slotToRef(d: DimensionSlot): DimensionRef {
  const out: DimensionRef = { key: d.key };
  if (d.grain && (d.date || KNOWN_DATE_DIMENSIONS.has(d.key))) out.grain = d.grain;
  if (d.choices.length > 0) out.choices = [...d.choices];
  if (d.topN !== null) out.top_n = d.topN;
  return out;
}

/** Issues that only matter when saving; they never block `toDefinition`. */
const SAVE_ONLY_ISSUES: ReadonlySet<string> = new Set(["nameRequired", "groupsRequired"]);

export function toDefinition(state: BuilderState): ReportDefinition | null {
  if (validate(state, undefined).some((i) => !SAVE_ONLY_ISSUES.has(i.key))) return null;
  const slots = orderedSlots(state);
  const viz: Visualization = { type: state.visualization.type ?? "table" };
  if (state.columns.length > 0) viz.series_dimension = slots.indexOf(state.columns[0]);
  if (state.visualization.chart_measures && state.visualization.chart_measures.length > 0) {
    viz.chart_measures = [...state.visualization.chart_measures];
  }
  const out: ReportDefinition = {
    schema_version: 1,
    source: state.source as string,
    dimensions: slots.map(slotToRef),
    measures: state.measures.map((key) => ({ key })),
    visualization: viz,
    sort: state.sort.map((s) => ({ ...s })),
    totals: { ...state.totals },
  };
  if (state.filter && state.filter.where) out.filter = structuredClone(state.filter);
  if (state.parameters.length > 0) out.parameters = structuredClone(state.parameters);
  if (state.timeField) out.time_field = state.timeField;
  if (state.timeRange) out.time_range = { ...state.timeRange };
  if (state.timeZone) out.time_zone = state.timeZone;
  if (state.limit !== null) out.limit = state.limit;
  return out;
}

// --- validation -------------------------------------------------------------

const ALL_VISUALIZATIONS: readonly VisualizationType[] = [
  "table",
  "pivot",
  "bar",
  "stacked_bar",
  "line",
  "area",
  "donut",
  "heatmap",
  "kpi",
];

/** i18n key (under reports.builder.issues) why `viz` cannot draw this shape, or null. */
function vizShapeIssue(viz: VisualizationType, dims: number, measures: number, plotted: number): string | null {
  switch (viz) {
    case "table":
      return null;
    case "pivot":
      return dims === 2 ? null : "vizNeedsTwoDimensions";
    case "bar":
    case "line":
    case "area":
      return dims >= 1 ? null : "vizNeedsOneDimension";
    case "stacked_bar":
      return dims >= 2 || measures >= 2 ? null : "vizNeedsTwoDimensions";
    case "donut":
      if (dims !== 1) return "vizNeedsOneDimension";
      return plotted === 1 ? null : "vizNeedsOneMeasure";
    case "heatmap":
      if (dims !== 2) return "vizNeedsTwoDimensions";
      return plotted === 1 ? null : "vizNeedsOneMeasure";
    case "kpi":
      return dims === 0 ? null : "vizNeedsNoDimension";
  }
}

function plottedMeasures(state: BuilderState): number {
  const cm = state.visualization.chart_measures;
  return cm && cm.length > 0 ? cm.length : state.measures.length;
}

export function allowedVisualizations(state: BuilderState, source: ReportSource | undefined): VisualizationType[] {
  void source; // every source draws the same shapes; kept for plugin sources with limits
  const dims = dimensionCount(state);
  const plotted = plottedMeasures(state);
  return ALL_VISUALIZATIONS.filter((v) => vizShapeIssue(v, dims, state.measures.length, plotted) === null);
}

export type ValidateMeta = {
  /** Check the name (save dialog). */
  name?: boolean;
  /** The user chose "groups" visibility but picked none (save dialog). */
  groupsMissing?: boolean;
};

const PARAMETER_KEY = /^[a-z][a-z0-9_]{0,39}$/;

/** Mirrors the backend `_check_parameter` value rules: ints for id/int fields, strings otherwise, enum members. */
function parameterValueOk(v: string | number, field: { type: string; enumValues: { value: string }[] }): boolean {
  if (field.type === "id" || field.type === "int") return typeof v === "number" && Number.isInteger(v);
  if (typeof v !== "string") return false;
  return field.type !== "enum" || field.enumValues.some((e) => e.value === v);
}

// Only keys are looked up; no translation happens here.
const keyOnly = ((k: string) => k) as unknown as TFunction;

export function validate(state: BuilderState, source: ReportSource | undefined, meta?: ValidateMeta): BuilderIssue[] {
  const issues: BuilderIssue[] = [];
  const add = (path: string, key: string) => issues.push({ path, key });

  if (!state.source) add("source", "noSource");
  if (state.measures.length === 0) add("measures", "noMeasure");
  if (dimensionCount(state) > MAX_DIMENSIONS) add("dimensions", "tooManyDimensions");
  if (state.measures.length > MAX_MEASURES) add("measures", "tooManyMeasures");

  const vizIssue = vizShapeIssue(
    state.visualization.type ?? "table",
    dimensionCount(state),
    state.measures.length,
    plottedMeasures(state),
  );
  if (vizIssue) add("visualization", vizIssue);

  if (source) {
    const fields = fieldsFromSource(source, keyOnly);
    if (state.filter && ticketFilterErrors(state.filter, fields).size > 0) add("filter", "filterInvalid");
  }
  const byKey = new Map((source ? fieldsFromSource(source, keyOnly) : []).map((f) => [f.key, f]));
  const seen = new Set<string>();
  state.parameters.forEach((p, i) => {
    if (seen.has(p.key)) add(`parameters.${i}.key`, "duplicateParameter");
    seen.add(p.key);
    if (!PARAMETER_KEY.test(p.key)) add(`parameters.${i}.key`, "parameterKeyInvalid");
    if (!source) return;
    const field = byKey.get(p.field);
    if (!field) {
      add(`parameters.${i}.field`, "parameterFieldUnknown");
      return;
    }
    if (!field.ops.includes(p.op ?? "in")) add(`parameters.${i}.op`, "parameterOpInvalid");
    for (const name of ["allowed_values", "default"] as const) {
      const bad = (p[name] ?? []).some((v) => !parameterValueOk(v, field));
      if (bad) add(`parameters.${i}.${name}`, "parameterValueInvalid");
    }
    if (p.allowed_values && p.default && p.default.some((v) => !p.allowed_values!.includes(v))) {
      add(`parameters.${i}.default`, "parameterValueInvalid");
    }
  });

  const sortable = new Set([...state.rows, ...state.columns].map((d) => d.key).concat(state.measures));
  if (state.sort.length > 3 || state.sort.some((s) => !sortable.has(s.key))) add("sort", "sortInvalid");
  if (state.limit !== null && !(Number.isInteger(state.limit) && state.limit >= 1 && state.limit <= 10_000)) {
    add("limit", "limitInvalid");
  }
  if ([...state.rows, ...state.columns].some((d) => d.topN !== null && !(Number.isInteger(d.topN) && d.topN >= 1 && d.topN <= 100))) {
    add("dimensions", "topNInvalid");
  }
  const tr = state.timeRange;
  if (tr) {
    const bad =
      tr.kind === "absolute"
        ? !tr.start || !tr.end || tr.end < tr.start
        : !Number.isInteger(tr.count) || tr.count < 1 || tr.count > 3660;
    if (bad) add("timeRange", "timeRangeInvalid");
  }

  if (meta?.name && state.name.trim() === "") add("name", "nameRequired");
  if (meta?.groupsMissing) add("groups", "groupsRequired");
  return issues;
}
