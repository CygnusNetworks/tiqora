import { dateRangeForPreset, type DateRangePreset } from "@/lib/dateRanges";
import type { Grain, ReportDefinition, ReportRunIn } from "@/lib/reportsApi";
import { displayTimeZone } from "@/lib/timeZone";

/** Filter values of one report view. `preset: null` = the report's own default range. */
export type ReportFilterState = {
  preset: DateRangePreset | "custom" | null;
  from: string;
  to: string;
  grain: Grain | null;
  choice: string | null;
  queues: number[];
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

/** Absolute dates of the filter: a named preset is recomputed for today (bookmarks stay relative). */
export function filterRange(state: ReportFilterState): { from: string; to: string } | null {
  if (state.preset === null || state.preset === "reset") return null;
  if (state.preset === "custom") return state.from && state.to ? { from: state.from, to: state.to } : null;
  return dateRangeForPreset(state.preset);
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
    compare_previous: false,
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
  if (state.queues.length > 0) body.parameters = { queues: state.queues };
  return body;
}
