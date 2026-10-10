import type { DateRangePreset } from "@/lib/dateRanges";
import type { ReportDefinition } from "@/lib/reportsApi";

type TimeRange = NonNullable<ReportDefinition["time_range"]>;
type Relative = Extract<TimeRange, { kind: "relative" }>;

/**
 * The range presets of the report filter bar, stored as relative ranges so a saved report
 * keeps meaning "this month" next month (the viewer's filter bar then offers the same presets).
 */
export const BUILDER_PRESETS: readonly Exclude<DateRangePreset, "reset">[] = [
  "today",
  "yesterday",
  "thisWeek",
  "last7Days",
  "last30Days",
  "thisMonth",
  "lastMonth",
  "thisQuarter",
  "lastQuarter",
  "thisYear",
  "lastYear",
];

type Spec = Pick<Relative, "unit" | "count" | "include_current">;

const SPECS: Record<(typeof BUILDER_PRESETS)[number], Spec> = {
  today: { unit: "day", count: 1, include_current: true },
  yesterday: { unit: "day", count: 1, include_current: false },
  last7Days: { unit: "day", count: 7, include_current: true },
  last30Days: { unit: "day", count: 30, include_current: true },
  thisWeek: { unit: "week", count: 1, include_current: true },
  thisMonth: { unit: "month", count: 1, include_current: true },
  lastMonth: { unit: "month", count: 1, include_current: false },
  thisQuarter: { unit: "quarter", count: 1, include_current: true },
  lastQuarter: { unit: "quarter", count: 1, include_current: false },
  thisYear: { unit: "year", count: 1, include_current: true },
  lastYear: { unit: "year", count: 1, include_current: false },
};

export function presetRange(preset: (typeof BUILDER_PRESETS)[number]): TimeRange {
  return { kind: "relative", ...SPECS[preset] };
}

/** Which preset a stored range is, "custom" for any other range, null for none. */
export function presetOf(range: TimeRange | null | undefined): (typeof BUILDER_PRESETS)[number] | "custom" | null {
  if (!range) return null;
  if (range.kind === "relative") {
    const hit = BUILDER_PRESETS.find((p) => {
      const s = SPECS[p];
      return s.unit === range.unit && s.count === range.count && s.include_current === range.include_current;
    });
    if (hit) return hit;
  }
  return "custom";
}
