import { dateRangeForPreset, type DateRangePreset } from "@/lib/dateRanges";
import type { Grain, ReportDefinition, ReportParameter, ReportRunIn } from "@/lib/reportsApi";
import { displayTimeZone } from "@/lib/timeZone";

/** A runtime parameter's value: chosen values (`in`/`eq`) or on/off (`is_true`/`is_false`). */
export type ReportParamValue = (string | number)[] | boolean | null;
export type ReportParamValues = Record<string, ReportParamValue>;

/** Filter values of one report view. `preset: null` = the report's own default range. */
export type ReportFilterState = {
  preset: DateRangePreset | "custom" | null;
  from: string;
  to: string;
  grain: Grain | null;
  choice: string | null;
  queues: number[];
  /** Runtime parameters other than the queues (`ReportParameterControls`). */
  params?: ReportParamValues;
};

export const EMPTY_FILTER: ReportFilterState = {
  preset: null,
  from: "",
  to: "",
  grain: null,
  choice: null,
  queues: [],
};

/** Dimension keys whose values are calendar buckets (they carry a grain). */
const DATE_DIMENSIONS = new Set(["created", "closed", "occurred", "snapshot", "booked", "first_response"]);

/** Index of the dimension that offers runtime choices, or -1. */
export function choiceIndex(definition: ReportDefinition): number {
  return (definition.dimensions ?? []).findIndex((d) => (d.choices ?? []).length > 0);
}

/** The dimension keys after the runtime choice is applied. */
export function effectiveDimensions(definition: ReportDefinition, choice: string | null): string[] {
  const at = choiceIndex(definition);
  return (definition.dimensions ?? []).map((d, i) => (i === at && choice ? choice : d.key));
}

/** Grain of the first date dimension (the one the run's `grain` replaces), or null. */
export function dateDimensionGrain(definition: ReportDefinition, choice: string | null): Grain | null {
  const keys = effectiveDimensions(definition, choice);
  const i = keys.findIndex((k) => DATE_DIMENSIONS.has(k));
  if (i < 0) return null;
  const ref = definition.dimensions?.[i];
  return ((ref?.key === keys[i] ? ref.grain : null) ?? "day") as Grain;
}

/** Whether the date range applies: snapshot reports ("open now") have no time range. */
export function hasTimeRange(definition: ReportDefinition): boolean {
  return definition.time_range != null;
}

export function hasQueueParameter(definition: ReportDefinition): boolean {
  return (definition.parameters ?? []).some((p) => p.key === "queues");
}

const isBoolOp = (p: ReportParameter) => p.op === "is_true" || p.op === "is_false";

/** The parameters with their own control; the queues have the filter bar. */
export function runtimeParameters(definition: ReportDefinition): ReportParameter[] {
  return (definition.parameters ?? []).filter((p) => p.key !== "queues");
}

/**
 * What a run gets from the chosen values: only set ones, only the definition's parameters. A required
 * parameter without a choice takes its default (the server insists on a value even when it has one).
 */
export function effectiveParams(definition: ReportDefinition, values: ReportParamValues | undefined): ReportParamValues {
  const out: ReportParamValues = {};
  for (const p of runtimeParameters(definition)) {
    const v = values?.[p.key];
    if (isBoolOp(p)) {
      if (v === true) out[p.key] = true;
    } else if (Array.isArray(v) && v.length > 0) {
      out[p.key] = v;
    } else if (p.required && p.default && p.default.length > 0) {
      out[p.key] = [...p.default];
    }
  }
  return out;
}

/** Required parameters nothing was chosen for: the run waits for them. */
export function missingRequired(definition: ReportDefinition, values: ReportParamValues | undefined): ReportParameter[] {
  const given = effectiveParams(definition, values);
  return runtimeParameters(definition).filter((p) => p.required && given[p.key] === undefined);
}

/** Absolute dates of the filter: a named preset is recomputed for today (bookmarks stay relative). */
export function filterRange(state: ReportFilterState): { from: string; to: string } | null {
  if (state.preset === null || state.preset === "reset") return null;
  if (state.preset === "custom") return state.from && state.to ? { from: state.from, to: state.to } : null;
  return dateRangeForPreset(state.preset);
}

/** A typed own range whose start lies after its end (never sent: the server would answer 422). */
export function rangeInvalid(state: ReportFilterState): boolean {
  return state.preset === "custom" && state.from !== "" && state.to !== "" && state.from > state.to;
}

/** Builds the run request; the definition's own range applies while no range is chosen. */
export function toRunIn(
  reportKey: string,
  state: ReportFilterState,
  definition?: ReportDefinition,
): ReportRunIn {
  const body: ReportRunIn = {
    report_key: reportKey,
    time_zone: displayTimeZone(),
    // Key figures always come with the previous period (the tiles show the change).
    compare_previous: definition?.visualization?.type === "kpi",
    force_job: false,
    no_cache: false,
  };
  const range = filterRange(state);
  if (range && (!definition || hasTimeRange(definition))) {
    body.time_range = { kind: "absolute", start: range.from, end: range.to };
  }
  if (state.grain) body.grain = state.grain;
  const at = definition ? choiceIndex(definition) : 0;
  if (state.choice && at >= 0) body.dimension_choices = { [String(at)]: state.choice };
  const parameters: NonNullable<ReportRunIn["parameters"]> = definition
    ? effectiveParams(definition, state.params)
    : {};
  if (state.queues.length > 0) parameters.queues = state.queues;
  if (Object.keys(parameters).length > 0) body.parameters = parameters;
  return body;
}
