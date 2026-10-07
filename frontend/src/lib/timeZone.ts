/**
 * The agent's display time zone and Intl-only helpers to compute in it.
 *
 * The backend stores UTC; the UI shows and interprets wall-clock times in the
 * agent's Znuny `UserTimeZone` preference, falling back to the browser zone.
 * `AuthProvider` feeds the preference in via {@link setDisplayTimeZone} while
 * rendering, so formatting helpers read the right zone without every call
 * site threading it through. All helpers take an explicit `zone` (default:
 * the display zone) so tests stay independent of the machine's TZ.
 */

const FALLBACK_ZONE = "UTC";

/** The browser's IANA zone ("UTC" if the engine does not report one). */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || FALLBACK_ZONE;
  } catch {
    return FALLBACK_ZONE;
  }
}

/** Whether `Intl` accepts `zone` — an unknown stored preference must fall
 * back instead of throwing a RangeError on every formatted date. */
export function isValidTimeZone(zone: string | null | undefined): zone is string {
  if (!zone) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

let preferredZone: string | null = null;

/** Set the agent's preference (`null` = follow the browser). Invalid zones
 * are ignored. Returns the effective zone. */
export function setDisplayTimeZone(zone: string | null | undefined): string {
  preferredZone = isValidTimeZone(zone) ? zone : null;
  return displayTimeZone();
}

/** The zone the UI shows times in: the agent's preference, else the browser's. */
export function displayTimeZone(): string {
  return preferredZone ?? browserTimeZone();
}

/** Effective zone for a `/auth/me` payload without touching the module state. */
export function effectiveTimeZone(preference: string | null | undefined): string {
  return isValidTimeZone(preference) ? preference : browserTimeZone();
}

export type ZonedParts = {
  year: number;
  /** 1–12 */
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
};

const partsFormatters = new Map<string, Intl.DateTimeFormat>();

function partsFormatter(zone: string): Intl.DateTimeFormat {
  let fmt = partsFormatters.get(zone);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      // h23, not hour12:false — some engines render midnight as "24" then.
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    partsFormatters.set(zone, fmt);
  }
  return fmt;
}

/** Wall-clock fields of `date` in `zone`. */
export function zonedParts(date: Date, zone: string = displayTimeZone()): ZonedParts {
  const out: ZonedParts = { year: 0, month: 0, day: 0, hour: 0, minute: 0, second: 0 };
  for (const part of partsFormatter(zone).formatToParts(date)) {
    if (part.type in out) out[part.type as keyof ZonedParts] = Number(part.value);
  }
  return out;
}

/** UTC offset of `zone` at the instant `ms`, in milliseconds (east positive). */
function offsetMs(ms: number, zone: string): number {
  const p = zonedParts(new Date(ms), zone);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return asUtc - (ms - (((ms % 1000) + 1000) % 1000));
}

const pad = (n: number, width = 2) => String(n).padStart(width, "0");

function ymdOf(p: { year: number; month: number; day: number }): string {
  return `${pad(p.year, 4)}-${pad(p.month)}-${pad(p.day)}`;
}

/** Calendar day (`YYYY-MM-DD`) of an instant in `zone`. */
export function ymdInZone(value: Date | string, zone: string = displayTimeZone()): string {
  const d = typeof value === "string" ? new Date(value) : value;
  return ymdOf(zonedParts(d, zone));
}

const YMD_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const HM_RE = /^(\d{2}):(\d{2})(?::(\d{2}))?$/;

function parseYmd(ymd: string): [number, number, number] | null {
  const m = YMD_RE.exec(ymd);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** `ymd` shifted by `days` calendar days (pure date math, no zone involved). */
export function addDaysYmd(ymd: string, days: number): string {
  const p = parseYmd(ymd);
  if (!p) return ymd;
  const d = new Date(Date.UTC(p[0], p[1] - 1, p[2] + days));
  return ymdOf({ year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() });
}

/**
 * The instant at which the wall clock in `zone` shows `ymd` `hm`.
 *
 * DST: a time inside a spring-forward gap (02:30 on the switch day in
 * Europe/Berlin) does not exist and resolves to the same distance after the
 * gap (03:30 summer time), like `new Date(y, m, d, h)` does locally; an
 * ambiguous time in the autumn overlap resolves to its first occurrence.
 * Returns null for malformed input.
 */
export function zonedWallTimeToUtc(
  ymd: string,
  hm: string,
  zone: string = displayTimeZone(),
): Date | null {
  const d = parseYmd(ymd);
  const t = HM_RE.exec(hm);
  if (!d || !t) return null;
  const wall = Date.UTC(d[0], d[1] - 1, d[2], Number(t[1]), Number(t[2]), Number(t[3] ?? 0));
  if (Number.isNaN(wall)) return null;
  // The offsets a day before and after bracket any single DST transition.
  const before = offsetMs(wall - 86_400_000, zone);
  const after = offsetMs(wall + 86_400_000, zone);
  const valid = [wall - before, wall - after].filter((ms) => ms + offsetMs(ms, zone) === wall);
  // Overlap: both readings are valid → the earlier instant. Gap: none is →
  // apply the pre-transition offset, which lands after the gap.
  const ms = valid.length > 0 ? Math.min(...valid) : wall - before;
  return new Date(ms);
}

/** First instant of the calendar day `ymd` in `zone`, as ISO (UTC). */
export function zonedDayStartIso(ymd: string, zone: string = displayTimeZone()): string | null {
  return zonedWallTimeToUtc(ymd, "00:00", zone)?.toISOString() ?? null;
}

/** Last millisecond of the calendar day `ymd` in `zone`, as ISO (UTC). */
export function zonedDayEndIso(ymd: string, zone: string = displayTimeZone()): string | null {
  const next = zonedWallTimeToUtc(addDaysYmd(ymd, 1), "00:00", zone);
  return next ? new Date(next.getTime() - 1).toISOString() : null;
}

/** `<input type="datetime-local">` value (`YYYY-MM-DDTHH:mm`) of an instant in `zone`. */
export function toZonedInputValue(
  value: Date | string | null | undefined,
  zone: string = displayTimeZone(),
): string {
  if (!value) return "";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "";
  const p = zonedParts(d, zone);
  return `${ymdOf(p)}T${pad(p.hour)}:${pad(p.minute)}`;
}

/** Instant for a `datetime-local` value read as wall time in `zone` (null when blank/invalid). */
export function fromZonedInputValue(
  value: string | null | undefined,
  zone: string = displayTimeZone(),
): Date | null {
  if (!value) return null;
  const [ymd, hm] = value.split("T");
  return hm ? zonedWallTimeToUtc(ymd, hm, zone) : null;
}

/**
 * A browser-local `Date` whose local fields equal the wall clock of `date`
 * in `zone`. Lets existing local-calendar math (week starts, month ends,
 * `formatYmd`) run on the zone's "today". Only meaningful for its
 * year/month/day/time fields — never send it to the API.
 */
export function wallClockDate(date: Date = new Date(), zone: string = displayTimeZone()): Date {
  const p = zonedParts(date, zone);
  return new Date(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
}

/** A small set used where `Intl.supportedValuesOf` is unavailable. */
const FALLBACK_ZONES = [
  "UTC",
  "Europe/London",
  "Europe/Lisbon",
  "Europe/Dublin",
  "Europe/Berlin",
  "Europe/Amsterdam",
  "Europe/Brussels",
  "Europe/Paris",
  "Europe/Madrid",
  "Europe/Rome",
  "Europe/Vienna",
  "Europe/Zurich",
  "Europe/Prague",
  "Europe/Warsaw",
  "Europe/Stockholm",
  "Europe/Oslo",
  "Europe/Copenhagen",
  "Europe/Helsinki",
  "Europe/Athens",
  "Europe/Bucharest",
  "Europe/Istanbul",
  "Europe/Kyiv",
  "Europe/Moscow",
  "Africa/Cairo",
  "Africa/Johannesburg",
  "Africa/Lagos",
  "Africa/Nairobi",
  "Asia/Dubai",
  "Asia/Karachi",
  "Asia/Kolkata",
  "Asia/Bangkok",
  "Asia/Singapore",
  "Asia/Shanghai",
  "Asia/Hong_Kong",
  "Asia/Tokyo",
  "Asia/Seoul",
  "Australia/Perth",
  "Australia/Sydney",
  "Pacific/Auckland",
  "America/Sao_Paulo",
  "America/Argentina/Buenos_Aires",
  "America/Bogota",
  "America/Mexico_City",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
];

/** All IANA zones the engine knows, sorted; a fixed list on old engines. */
export function listTimeZones(): string[] {
  let zones: string[] = [];
  try {
    if (typeof Intl.supportedValuesOf === "function") zones = Intl.supportedValuesOf("timeZone");
  } catch {
    zones = [];
  }
  const set = new Set(zones.length > 0 ? zones : FALLBACK_ZONES);
  // Some engines omit "UTC" from the canonical list.
  set.add("UTC");
  return [...set].sort((a, b) => a.localeCompare(b));
}

export type TimeZoneMismatch =
  /** No preference; the browser differs from the system default mails use. */
  | { kind: "browserVsSystem"; browser: string; system: string }
  /** A preference is set and this device is elsewhere. */
  | { kind: "settingVsDevice"; browser: string; zone: string };

/** Which time-zone notice applies, if any — Znuny's
 * ShowUserTimeZoneSelectionNotification, plus the travelling case. */
export function timeZoneMismatch(
  preference: string | null | undefined,
  systemDefault: string | null | undefined,
  browser: string,
): TimeZoneMismatch | null {
  if (isValidTimeZone(preference)) {
    return preference !== browser ? { kind: "settingVsDevice", browser, zone: preference } : null;
  }
  if (typeof systemDefault === "string" && systemDefault && systemDefault !== browser) {
    return { kind: "browserVsSystem", browser, system: systemDefault };
  }
  return null;
}

let zoneOptionsCache: { value: string; label: string; hint: string }[] | null = null;

/** Picker rows for every zone: readable name plus its current GMT offset
 * (also searchable). Built once — a few hundred Intl formatters. */
export function timeZoneOptions(): { value: string; label: string; hint: string }[] {
  if (!zoneOptionsCache) {
    const now = new Date();
    zoneOptionsCache = listTimeZones().map((zone) => ({
      value: zone,
      label: zone.replace(/_/g, " "),
      hint: zoneOffsetLabel(zone, now),
    }));
  }
  return zoneOptionsCache;
}

/** "GMT+2" / "GMT-5:30" label for `zone` at `date`, or "" if unsupported. */
export function zoneOffsetLabel(zone: string, date: Date = new Date()): string {
  try {
    const part = new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "shortOffset" })
      .formatToParts(date)
      .find((p) => p.type === "timeZoneName");
    return part?.value ?? "";
  } catch {
    return "";
  }
}
