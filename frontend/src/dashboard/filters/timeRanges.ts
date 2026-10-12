import type { DashboardFilters } from "@/lib/dashboardsApi";

/** The dashboard time-range presets (backend `TimeRangePreset`), in popover order. */
export type TimeRangePreset = NonNullable<DashboardFilters["time_range"]>;

export const TIME_RANGE_PRESETS: readonly TimeRangePreset[] = ["today", "7d", "30d", "90d", "this_month", "last_month"];

/** i18n key of a preset's label ("Letzte 30 Tage"). */
export function timeRangeLabelKey(preset: TimeRangePreset): string {
  return `dashboards.filters.ranges.${preset}`;
}
