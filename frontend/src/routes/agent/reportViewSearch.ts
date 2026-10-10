import {
  choiceIndex,
  runtimeParameters,
  type ReportFilterState,
  type ReportParamValues,
} from "@/components/reports/reportFilter";
import { DATE_RANGE_PRESETS, type DateRangePreset } from "@/lib/dateRanges";
import { GRAINS, type Grain, type ReportDefinition } from "@/lib/reportsApi";

/** Address of a report view: the filter, so a link or bookmark opens the same report. */
export type ReportViewSearch = {
  preset?: string;
  from?: string;
  to?: string;
  grain?: Grain;
  choice?: string;
  /** Queue ids, comma-separated. */
  queues?: string;
  /** Runtime parameters (not the queues): lists of values, or `true` for a switched-on yes/no parameter. */
  params?: ReportParamValues;
};

const isoDate = (v: unknown): string | undefined =>
  typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined;

const PARAM_KEY = /^[a-z][a-z0-9_]{0,39}$/;
const MAX_PARAMS = 10;
const MAX_PARAM_VALUES = 1000;

const paramValue = (v: unknown): string | number | undefined =>
  (typeof v === "string" && v.length > 0 && v.length <= 1000) || (typeof v === "number" && Number.isSafeInteger(v))
    ? v
    : undefined;

/**
 * `params` from the address. TanStack parses the JSON of search values, so numbers arrive as numbers; the
 * object may still come as its JSON text. A lone value counts as a list of one. Anything else is dropped.
 */
function normalizeParams(raw: unknown): ReportParamValues | undefined {
  let obj = raw;
  if (typeof obj === "string") {
    try {
      obj = JSON.parse(obj);
    } catch {
      return undefined;
    }
  }
  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) return undefined;
  const out: ReportParamValues = {};
  for (const [key, v] of Object.entries(obj)) {
    if (Object.keys(out).length >= MAX_PARAMS) break;
    if (!PARAM_KEY.test(key)) continue;
    if (typeof v === "boolean") {
      out[key] = v;
      continue;
    }
    const list = (Array.isArray(v) ? v : [v])
      .slice(0, MAX_PARAM_VALUES)
      .map(paramValue)
      .filter((x): x is string | number => x !== undefined);
    if (list.length > 0) out[key] = list;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

/** Router `validateSearch` of `/agent/reports/$reportKey`. */
export function validateReportViewSearch(s: Record<string, unknown>): ReportViewSearch {
  const preset =
    typeof s.preset === "string" && (s.preset === "custom" || (DATE_RANGE_PRESETS as string[]).includes(s.preset))
      ? s.preset
      : undefined;
  const grain = typeof s.grain === "string" && (GRAINS as readonly string[]).includes(s.grain) ? (s.grain as Grain) : undefined;
  const choice = typeof s.choice === "string" && /^[a-z_]{1,64}$/.test(s.choice) ? s.choice : undefined;
  const rawQueues = typeof s.queues === "number" ? String(s.queues) : typeof s.queues === "string" ? s.queues : "";
  const ids = rawQueues
    .split(",")
    .map((x) => Number(x))
    .filter((n) => Number.isInteger(n) && n > 0);
  return {
    preset,
    from: isoDate(s.from),
    to: isoDate(s.to),
    grain,
    choice,
    queues: ids.length > 0 ? ids.join(",") : undefined,
    params: normalizeParams(s.params),
  };
}

/** The parameter values that fit the definition: its runtime parameters only, one value for `eq`, on/off for yes/no. */
function paramsFromSearch(search: ReportViewSearch, definition?: ReportDefinition): ReportParamValues | undefined {
  if (!search.params) return undefined;
  if (!definition) return search.params;
  const out: ReportParamValues = {};
  for (const p of runtimeParameters(definition)) {
    const v = search.params[p.key];
    if (p.op === "is_true" || p.op === "is_false") {
      if (v === true) out[p.key] = true;
    } else if (Array.isArray(v) && v.length > 0) {
      out[p.key] = p.op === "eq" ? v.slice(0, 1) : v;
    }
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

/** Filter state from the address; unknown choices are dropped. */
export function filterFromSearch(search: ReportViewSearch, definition?: ReportDefinition): ReportFilterState {
  const named = search.preset && search.preset !== "custom" && search.preset !== "reset" ? (search.preset as DateRangePreset) : null;
  const custom = !named && search.from && search.to;
  const at = definition ? choiceIndex(definition) : -1;
  const dim = at >= 0 ? definition?.dimensions?.[at] : undefined;
  const choice = search.choice && dim?.choices?.includes(search.choice) ? search.choice : null;
  return {
    preset: named ?? (custom ? "custom" : null),
    from: custom ? (search.from as string) : "",
    to: custom ? (search.to as string) : "",
    grain: search.grain ?? null,
    choice,
    queues: (search.queues ?? "")
      .split(",")
      .map(Number)
      .filter((n) => Number.isInteger(n) && n > 0),
    params: paramsFromSearch(search, definition),
  };
}

/** Chosen values only: no empty lists, no switched-off yes/no. */
function cleanParams(params: ReportParamValues | undefined): ReportParamValues | undefined {
  const out: ReportParamValues = {};
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v === true || (Array.isArray(v) && v.length > 0)) out[k] = v;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

/** Address of a filter state; a named preset stays relative (no dates). */
export function searchFromFilter(state: ReportFilterState): ReportViewSearch {
  const custom = state.preset === "custom";
  return {
    preset: state.preset ?? undefined,
    from: custom && state.from ? state.from : undefined,
    to: custom && state.to ? state.to : undefined,
    grain: state.grain ?? undefined,
    choice: state.choice ?? undefined,
    queues: state.queues.length > 0 ? state.queues.join(",") : undefined,
    params: cleanParams(state.params),
  };
}
