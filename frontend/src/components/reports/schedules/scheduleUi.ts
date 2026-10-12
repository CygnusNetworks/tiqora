import type { ReportRun, ScheduleItemIn } from "@/lib/reportSchedulesApi";
import { displayTimeZone } from "@/lib/timeZone";

/** Non-component helpers of the schedule widgets (kept apart for fast refresh). */

/** Mutable only so tests can shorten the wait before the preview is asked again. */
export const recurrencePreviewDebounce = { ms: 400 };

/** A time (or sentence) of the schedule with its zone as suffix when that differs from the zone the page shows: "07:00 (Europe/Berlin)". */
export function zoneSuffix(time: string, scheduleZone: string): string {
  return scheduleZone && scheduleZone !== displayTimeZone() ? `${time} (${scheduleZone})` : time;
}

/** Poll interval while a delivery is queued or running. */
export const RUN_POLL_MS = 5_000;

export function runIsActive(r: ReportRun): boolean {
  return r.status === "queued" || r.status === "running";
}

type RelativeRange = { kind: "relative"; count: number; unit: "day" | "week" | "month" | "quarter" | "year"; include_current: boolean };

/** Rolling periods offered for a delivery; "default" keeps the report's own range. */
export const RANGE_PRESETS = {
  default: null,
  last7Days: { kind: "relative", count: 7, unit: "day", include_current: false },
  last30Days: { kind: "relative", count: 30, unit: "day", include_current: false },
  lastMonth: { kind: "relative", count: 1, unit: "month", include_current: false },
  lastQuarter: { kind: "relative", count: 1, unit: "quarter", include_current: false },
  lastYear: { kind: "relative", count: 1, unit: "year", include_current: false },
} as const satisfies Record<string, RelativeRange | null>;
export type PresetKey = keyof typeof RANGE_PRESETS;

type TimeRangeOverride = NonNullable<ScheduleItemIn["overrides"]>["time_range"];

/** Which preset an override is; anything else (an absolute range) is kept as "custom". */
export function presetOf(range: TimeRangeOverride): PresetKey | "custom" {
  if (range == null) return "default";
  if (range.kind !== "relative" || range.include_current) return "custom";
  for (const [key, preset] of Object.entries(RANGE_PRESETS) as [PresetKey, RelativeRange | null][]) {
    if (preset && preset.count === range.count && preset.unit === range.unit) return key;
  }
  return "custom";
}

