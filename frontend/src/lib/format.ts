/** Relative age / datetime helpers for agent tables.
 *
 * Absolute times render in the agent's display time zone (see `timeZone.ts`);
 * the trailing `timeZone` parameter overrides it (tests, explicit zones). */

import { displayTimeZone, ymdInZone } from "./timeZone";

export function formatAgeSeconds(
  ageSeconds: number | null | undefined,
  locale: string,
): string {
  if (ageSeconds == null || Number.isNaN(ageSeconds)) return "—";
  const abs = Math.max(0, Math.floor(ageSeconds));
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (abs < 60) return rtf.format(-abs, "second");
  if (abs < 3600) return rtf.format(-Math.floor(abs / 60), "minute");
  if (abs < 86400) return rtf.format(-Math.floor(abs / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(-Math.floor(abs / 86400), "day");
  return rtf.format(-Math.floor(abs / (86400 * 30)), "month");
}

/** Relative time from now in either direction: "3 days ago" / "in 6 days".
 *
 * `formatAgeSeconds` only looks backwards, but an outstanding invitation is
 * described by how long its link still has. */
export function formatRelative(
  value: string | Date | null | undefined,
  locale: string,
): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  const delta = Math.round((d.getTime() - Date.now()) / 1000);
  const abs = Math.abs(delta);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (abs < 60) return rtf.format(delta, "second");
  if (abs < 3600) return rtf.format(Math.round(delta / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(delta / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(delta / 86400), "day");
  return rtf.format(Math.round(delta / (86400 * 30)), "month");
}

export function formatDateTime(
  value: string | Date | null | undefined,
  locale: string,
  timeZone: string = displayTimeZone(),
): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone,
  }).format(d);
}

/** Date-only (no time-of-day) formatting of an instant, e.g. for expiry previews. */
export function formatDateOnly(
  value: string | Date | null | undefined,
  locale: string,
  timeZone: string = displayTimeZone(),
): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone,
  }).format(d);
}

/** A calendar day given as `YYYY-MM-DD` (a filter bound, a group key) — not
 * an instant, so no zone may shift it. Same style as `formatDateOnly` unless
 * `options` say otherwise. */
export function formatCalendarDay(
  ymd: string | null | undefined,
  locale: string,
  options: Intl.DateTimeFormatOptions = { year: "numeric", month: "2-digit", day: "2-digit" },
): string {
  const m = ymd ? /^(\d{4})-(\d{2})-(\d{2})/.exec(ymd) : null;
  if (!m) return "—";
  const noonUtc = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12));
  return new Intl.DateTimeFormat(locale, { ...options, timeZone: "UTC" }).format(noonUtc);
}

/** Time of day (hours and minutes) of an instant. */
export function formatTimeOfDay(
  value: string | Date | null | undefined,
  locale: string,
  timeZone: string = displayTimeZone(),
): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone }).format(
    d,
  );
}

export function formatBytes(size: string | number | null | undefined): string {
  if (size == null || size === "") return "—";
  const n = typeof size === "string" ? Number(size) : size;
  if (!Number.isFinite(n)) return String(size);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function isEscalated(epoch: number | undefined | null): boolean {
  if (!epoch || epoch <= 0) return false;
  return epoch * 1000 < Date.now();
}

export type DayBucket = "today" | "yesterday" | "week" | "older";

/** Days since the epoch of the calendar day an instant falls on in `zone`. */
function dayNumber(d: Date, zone: string): number {
  const [y, m, day] = ymdInZone(d, zone).split("-").map(Number);
  return Date.UTC(y, m - 1, day) / 86_400_000;
}

function calendarDaysAgo(d: Date, now: Date, zone: string): number {
  return dayNumber(now, zone) - dayNumber(d, zone);
}

/** Which inbox day group a timestamp falls into, by calendar day in the
 * display zone (not by 24h distance: 23:50 yesterday is "yesterday" at 00:10
 * today). */
export function dayBucket(
  value: string | Date | null | undefined,
  now = new Date(),
  timeZone: string = displayTimeZone(),
): DayBucket {
  if (!value) return "older";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "older";
  const days = calendarDaysAgo(d, now, timeZone);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return "week";
  return "older";
}

/** Compact mail-client style timestamp for the ticket list: the time of day
 * for today, `yesterdayLabel` for yesterday, weekday + time within the week,
 * otherwise the short date. */
export function formatListTime(
  value: string | Date | null | undefined,
  locale: string,
  yesterdayLabel: string,
  now = new Date(),
  timeZone: string = displayTimeZone(),
): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  const time = formatTimeOfDay(d, locale, timeZone);
  switch (dayBucket(d, now, timeZone)) {
    case "today":
      return time;
    case "yesterday":
      return yesterdayLabel;
    case "week":
      return `${new Intl.DateTimeFormat(locale, { weekday: "short", timeZone }).format(d)} ${time}`;
    default: {
      const sameYear = ymdInZone(d, timeZone).slice(0, 4) === ymdInZone(now, timeZone).slice(0, 4);
      return new Intl.DateTimeFormat(locale, {
        day: "2-digit",
        month: "2-digit",
        year: sameYear ? undefined : "2-digit",
        timeZone,
      }).format(d);
    }
  }
}
