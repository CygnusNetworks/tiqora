/**
 * Date-range presets for the time-accounting report.
 *
 * Thin layer over `dateRanges.ts`: same local-calendar-day semantics (never
 * `toISOString()`, which shifts to UTC), plus `lastWeek` and a reverse lookup
 * that maps a stored {from,to} back to its preset so the chip stays marked
 * after a reload from the URL.
 */
import { dateRangeForPreset, formatYmd, type DateRange } from "./dateRanges";

export type TimeRangePreset =
  | "today"
  | "yesterday"
  | "thisWeek"
  | "lastWeek"
  | "thisMonth"
  | "lastMonth"
  | "thisQuarter"
  | "lastQuarter"
  | "thisYear"
  | "lastYear";

/** Ordered for UI rendering: shortest period first. */
export const TIME_RANGE_PRESETS: readonly TimeRangePreset[] = [
  "today",
  "yesterday",
  "thisWeek",
  "lastWeek",
  "thisMonth",
  "lastMonth",
  "thisQuarter",
  "lastQuarter",
  "thisYear",
  "lastYear",
];

export type { DateRange };

/** Full Monday–Sunday ISO week containing `now`, shifted by `weeks`. */
function isoWeekRange(now: Date, weeks: number): DateRange {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7) + weeks * 7);
  const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6);
  return { from: formatYmd(start), to: formatYmd(end) };
}

/**
 * Inclusive local date range for a preset as YYYY-MM-DD.
 * Running periods (this week/month/quarter/year) end today; closed periods
 * cover their full span. Pass `now` for deterministic tests.
 */
export function rangeForPreset(
  preset: TimeRangePreset,
  now: Date = new Date(),
): DateRange {
  if (preset === "lastWeek") return isoWeekRange(now, -1);
  return dateRangeForPreset(preset, now);
}

/**
 * The preset matching `from`/`to`, or null for a hand-picked range.
 * On days where several presets collapse to the same span (e.g. 1 January,
 * where today/thisMonth/thisYear all start and end on that day) the first
 * entry of `TIME_RANGE_PRESETS` wins.
 */
export function presetForRange(
  from: string | undefined,
  to: string | undefined,
  now: Date = new Date(),
): TimeRangePreset | null {
  if (!from || !to) return null;
  return (
    TIME_RANGE_PRESETS.find((preset) => {
      const range = rangeForPreset(preset, now);
      return range.from === from && range.to === to;
    }) ?? null
  );
}
