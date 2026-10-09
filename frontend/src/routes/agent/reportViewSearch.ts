import { choiceIndex, type ReportFilterState } from "@/components/reports/reportFilter";
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
};

const isoDate = (v: unknown): string | undefined =>
  typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined;

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
  };
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
  };
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
  };
}
