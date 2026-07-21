/** Pure date-range presets for stats/report filters (local calendar days). */

export type DateRangePreset =
  | "today"
  | "yesterday"
  | "thisWeek"
  | "last7Days"
  | "thisMonth"
  | "lastMonth"
  | "thisQuarter"
  | "lastQuarter"
  | "thisYear"
  | "lastYear"
  | "last30Days"
  | "reset";

/** Ordered list for UI rendering (reset last). */
export const DATE_RANGE_PRESETS: DateRangePreset[] = [
  "today",
  "yesterday",
  "thisWeek",
  "last7Days",
  "thisMonth",
  "lastMonth",
  "thisQuarter",
  "lastQuarter",
  "thisYear",
  "lastYear",
  "last30Days",
  "reset",
];

export type DateRange = { from: string; to: string };

/** Format a local Date as YYYY-MM-DD. */
export function formatYmd(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function startOfLocalDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function addDays(d: Date, days: number): Date {
  const out = startOfLocalDay(d);
  out.setDate(out.getDate() + days);
  return out;
}

/** Monday-first ISO week start (local). */
function startOfIsoWeek(d: Date): Date {
  const out = startOfLocalDay(d);
  const dow = (out.getDay() + 6) % 7; // 0=Mon .. 6=Sun
  return addDays(out, -dow);
}

function firstOfMonth(year: number, month: number): Date {
  return new Date(year, month, 1);
}

/** Last calendar day of `month` (0-indexed) via day 0 of the next month. */
function lastOfMonth(year: number, month: number): Date {
  return new Date(year, month + 1, 0);
}

/**
 * Map a preset key to an inclusive local date range as YYYY-MM-DD strings.
 * Open-ended presets (this week/month/quarter/year) run from period start through `now`.
 * Closed presets (last month/quarter/year, last N days) cover the full prior period.
 * Pass `now` for deterministic tests; defaults to the current local time.
 */
export function dateRangeForPreset(
  preset: DateRangePreset,
  now: Date = new Date(),
): DateRange {
  if (preset === "reset") {
    return { from: "", to: "" };
  }

  const today = startOfLocalDay(now);
  const y = today.getFullYear();
  const m = today.getMonth(); // 0-11
  const todayStr = formatYmd(today);

  switch (preset) {
    case "today":
      return { from: todayStr, to: todayStr };

    case "yesterday": {
      const yday = addDays(today, -1);
      const s = formatYmd(yday);
      return { from: s, to: s };
    }

    case "thisWeek":
      return { from: formatYmd(startOfIsoWeek(today)), to: todayStr };

    case "last7Days":
      return { from: formatYmd(addDays(today, -6)), to: todayStr };

    case "last30Days":
      return { from: formatYmd(addDays(today, -29)), to: todayStr };

    case "thisMonth":
      return { from: formatYmd(firstOfMonth(y, m)), to: todayStr };

    case "lastMonth": {
      // Day 0 of this month = last day of previous month
      const last = lastOfMonth(y, m - 1);
      const first = firstOfMonth(last.getFullYear(), last.getMonth());
      return { from: formatYmd(first), to: formatYmd(last) };
    }

    case "thisQuarter": {
      const qStartMonth = Math.floor(m / 3) * 3;
      return { from: formatYmd(firstOfMonth(y, qStartMonth)), to: todayStr };
    }

    case "lastQuarter": {
      const thisQStart = Math.floor(m / 3) * 3;
      // First day of this quarter, then day 0 = last day of previous quarter
      const lastDayPrevQ = new Date(y, thisQStart, 0);
      const prevQYear = lastDayPrevQ.getFullYear();
      const prevQStartMonth = lastDayPrevQ.getMonth() - 2; // last month of prev Q is M, start is M-2
      const first = firstOfMonth(prevQYear, prevQStartMonth);
      return { from: formatYmd(first), to: formatYmd(lastDayPrevQ) };
    }

    case "thisYear":
      return { from: formatYmd(firstOfMonth(y, 0)), to: todayStr };

    case "lastYear": {
      const prev = y - 1;
      return {
        from: formatYmd(firstOfMonth(prev, 0)),
        to: formatYmd(lastOfMonth(prev, 11)),
      };
    }

    default: {
      // Exhaustiveness guard — should be unreachable
      const _exhaustive: never = preset;
      return _exhaustive;
    }
  }
}

/** Whether the current filter values match a preset's computed range. */
export function isPresetActive(
  preset: DateRangePreset,
  dateFrom: string,
  dateTo: string,
  now: Date = new Date(),
): boolean {
  const range = dateRangeForPreset(preset, now);
  return range.from === dateFrom && range.to === dateTo;
}
