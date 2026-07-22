/** Expiry computation for the API-key create/edit form's preset picker. */

export type ExpiryPreset = "unlimited" | "30" | "90" | "180" | "365" | "custom";

const PRESET_DAYS: Record<Exclude<ExpiryPreset, "unlimited" | "custom">, number> = {
  "30": 30,
  "90": 90,
  "180": 180,
  "365": 365,
};

/**
 * Preset expiry lands on UTC end-of-day (23:59:59) N days from `now` — a key
 * created today with the "30 Tage" preset should still be valid the whole
 * 30th day, not expire a few hours early at the current time-of-day.
 */
export function presetToExpiresAt(
  preset: Exclude<ExpiryPreset, "unlimited" | "custom">,
  now: Date = new Date(),
): string {
  const d = new Date(now);
  d.setUTCDate(d.getUTCDate() + PRESET_DAYS[preset]);
  d.setUTCHours(23, 59, 59, 0);
  return d.toISOString();
}

/** Same end-of-day-UTC convention for an explicitly picked calendar date. */
export function dateToExpiresAt(dateStr: string): string {
  return `${dateStr}T23:59:59.000Z`;
}

/** Earliest selectable date for the native date input (today is too late for a *future* expiry). */
export function tomorrowDateStr(now: Date = new Date()): string {
  const d = new Date(now);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

export function isExpired(expiresAt: string | null | undefined, now: Date = new Date()): boolean {
  if (!expiresAt) return false;
  const d = new Date(expiresAt);
  return !Number.isNaN(d.getTime()) && d.getTime() < now.getTime();
}
