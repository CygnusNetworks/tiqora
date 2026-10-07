/** Pure date-grid math for the calendar month/week views (no date library).
 *
 * Grid cells are browser-local `Date`s that stand for calendar days of the
 * agent's display zone (their local fields are that zone's wall clock, see
 * `wallClockDate`); instants from the API are mapped onto them by
 * `ymdInZone`, and cells back to instants by `wallDateToUtcIso`. */

import { displayTimeZone, ymdInZone, zonedWallTimeToUtc } from "./timeZone";

export function startOfDay(d: Date): Date {
  const out = new Date(d);
  out.setHours(0, 0, 0, 0);
  return out;
}

export function addDays(d: Date, days: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + days);
  return out;
}

export function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/** Monday-first week start for a given date. */
export function startOfWeek(d: Date): Date {
  const out = startOfDay(d);
  const dow = (out.getDay() + 6) % 7; // 0=Mon .. 6=Sun
  return addDays(out, -dow);
}

/**
 * Build the 6x7 day grid for the month containing *anchor*, Monday-first,
 * including the leading/trailing days from adjacent months needed to fill
 * whole weeks.
 */
export function monthGridDays(anchor: Date): Date[] {
  const firstOfMonth = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const gridStart = startOfWeek(firstOfMonth);
  return Array.from({ length: 42 }, (_, i) => addDays(gridStart, i));
}

export function weekDays(anchor: Date): Date[] {
  const start = startOfWeek(anchor);
  return Array.from({ length: 7 }, (_, i) => addDays(start, i));
}

export function isCurrentMonth(d: Date, anchor: Date): boolean {
  return d.getMonth() === anchor.getMonth() && d.getFullYear() === anchor.getFullYear();
}

/** Group occurrences by their calendar day in `zone` (YYYY-MM-DD key, the
 * same as `dayKey` of the grid cell showing that day). */
export function groupByDay<T extends { start_time: string }>(
  items: T[],
  zone: string = displayTimeZone(),
): Map<string, T[]> {
  const map = new Map<string, T[]>();
  for (const item of items) {
    const key = ymdInZone(item.start_time, zone);
    const list = map.get(key);
    if (list) list.push(item);
    else map.set(key, [item]);
  }
  return map;
}

export function dayKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** The instant a grid `Date` (wall clock of `zone` in its local fields)
 * stands for, as ISO — for API range bounds. */
export function wallDateToUtcIso(d: Date, zone: string = displayTimeZone()): string {
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return (zonedWallTimeToUtc(dayKey(d), hm, zone) ?? d).toISOString();
}
