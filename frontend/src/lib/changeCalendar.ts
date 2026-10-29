/**
 * Pure helpers of the change calendar: the requested range per view (never more than 92 days, the
 * API's limit), the days a window spans and the freeze level of a day. The whole calendar runs in
 * UTC like the rest of the change screens ("Zeiten in UTC"), so days are `YYYY-MM-DD` keys of UTC.
 */
import { addDays, dayKey, monthGridDays, startOfWeek, weekDays } from "./calendarMath";
import { addDaysYmd, ymdInZone } from "./timeZone";
import type { CalendarChange, FreezeOccurrence } from "./changeApi";

export const CALENDAR_ZONE = "UTC";
export const MAX_RANGE_DAYS = 92;
/** The service table shows two weeks from the Monday of the anchor's week. */
export const SERVICE_DAYS = 14;

export type CalendarView = "month" | "week" | "service";

/** Grid days (wall clock = UTC) a view shows around `anchor`. */
export function viewDays(view: CalendarView, anchor: Date): Date[] {
  if (view === "month") return monthGridDays(anchor);
  if (view === "week") return weekDays(anchor);
  const first = startOfWeek(anchor);
  return Array.from({ length: SERVICE_DAYS }, (_, i) => addDays(first, i));
}

/** `[start, end)` of a view as UTC instants for the API; the span is at most `MAX_RANGE_DAYS`. */
export function viewRange(view: CalendarView, anchor: Date): { start: string; end: string; days: Date[] } {
  const days = viewDays(view, anchor).slice(0, MAX_RANGE_DAYS);
  return {
    start: `${dayKey(days[0])}T00:00:00Z`,
    end: `${dayKey(addDays(days[days.length - 1], 1))}T00:00:00Z`,
    days,
  };
}

/** Anchor after stepping `delta` pages of the view. */
export function stepAnchor(view: CalendarView, anchor: Date, delta: number): Date {
  if (view === "month") return new Date(anchor.getFullYear(), anchor.getMonth() + delta, 1);
  return addDays(anchor, delta * (view === "week" ? 7 : SERVICE_DAYS));
}

/** UTC days (`YYYY-MM-DD`) a window touches; an end exactly at midnight does not count as that day. */
export function spanDayKeys(startIso: string, endIso: string): string[] {
  const startMs = Date.parse(startIso);
  const endMs = Math.max(Date.parse(endIso) - 1, startMs);
  const last = ymdInZone(new Date(endMs), CALENDAR_ZONE);
  const keys: string[] = [];
  for (let key = ymdInZone(new Date(startMs), CALENDAR_ZONE); key <= last && keys.length < MAX_RANGE_DAYS * 2; key = addDaysYmd(key, 1)) {
    keys.push(key);
  }
  return keys;
}

/** Whether the window `[start, end)` overlaps the UTC day `key`. */
export function overlapsDay(startIso: string, endIso: string, key: string): boolean {
  return Date.parse(startIso) < Date.parse(`${addDaysYmd(key, 1)}T00:00:00Z`) && Date.parse(endIso) > Date.parse(`${key}T00:00:00Z`);
}

export type FreezeLevel = "block" | "warn";

/** The strictest enforcement of the freezes active on a day (`block` beats `warn`). */
export function dayFreezeLevel(freezes: readonly Pick<FreezeOccurrence, "enforcement" | "start" | "end">[], key: string): FreezeLevel | null {
  let level: FreezeLevel | null = null;
  for (const f of freezes) {
    if (!overlapsDay(f.start, f.end, key)) continue;
    if (f.enforcement === "block") return "block";
    level = "warn";
  }
  return level;
}

/** Freezes that apply when a service filter is set: global ones and those naming the service. */
export function freezesForService(freezes: readonly FreezeOccurrence[], serviceId: number | null): FreezeOccurrence[] {
  return serviceId === null ? [...freezes] : freezes.filter((f) => f.scope === "all" || f.service_ids.includes(serviceId));
}

/** Tailwind classes of a change bar by phase; theme tokens only. */
export const PHASE_BAR: Record<CalendarChange["phase"], string> = {
  draft: "bg-surface-subtle text-muted",
  submitted: "bg-accent-dim text-ink",
  assessment: "bg-accent-dim text-ink",
  approval: "bg-escalation/25 text-ink",
  scheduled: "bg-accent/30 text-ink",
  implementing: "bg-green/30 text-ink",
  review: "bg-green/15 text-ink",
  closed: "bg-surface-subtle text-muted",
  rejected: "bg-danger/20 text-ink",
  cancelled: "bg-surface-subtle text-muted",
};
