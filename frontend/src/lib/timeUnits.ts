/**
 * Znuny books time in fractional "time units" (`time_accounting.time_unit`),
 * so 15, 7.5 and 0.25 are all legal. Render them without trailing zeros —
 * "15" not "15.00", "7.5" not "7.50" — so the header counter stays narrow.
 */
export function formatTimeUnits(units: number): string {
  if (!Number.isFinite(units)) return "0";
  const rounded = Math.round(units * 100) / 100;
  return Number.isInteger(rounded) ? String(rounded) : String(rounded).replace(/0+$/, "");
}

/**
 * Display-side minutes/hours toggle for time-booking inputs. The stored value
 * is always whole minutes (Znuny's `time_unit` carries no unit of its own —
 * see `formatTimeUnits` above) — "hours" mode is purely how the field is
 * read/written, converted to/from minutes at the edges.
 */
export type TimeUnitMode = "min" | "hours";

/** Sensible quick-pick durations, always in minutes regardless of display mode. */
export const TIME_PRESET_MINUTES = [5, 15, 30, 45, 60] as const;

const TIME_UNIT_MODE_STORAGE_KEY = "tiqora-time-unit-mode";

/** Last-used minutes/hours choice, remembered per browser; defaults to minutes. */
export function loadTimeUnitMode(): TimeUnitMode {
  if (typeof window === "undefined") return "min";
  try {
    return window.localStorage.getItem(TIME_UNIT_MODE_STORAGE_KEY) === "hours" ? "hours" : "min";
  } catch {
    return "min";
  }
}

export function saveTimeUnitMode(mode: TimeUnitMode): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(TIME_UNIT_MODE_STORAGE_KEY, mode);
  } catch {
    // private mode / SSR — ignore
  }
}

/** Whole stored minutes -> the text a field in *mode* should display. */
export function minutesToDisplay(minutes: number, mode: TimeUnitMode): string {
  if (!Number.isFinite(minutes) || minutes <= 0) return "";
  return mode === "min" ? String(Math.round(minutes)) : formatTimeUnits(minutes / 60);
}

/**
 * Field text (already in *mode*'s unit) -> whole minutes to store, rounded so
 * bookings never land on fractional minutes (e.g. "0.2"). `null` for blank or
 * non-positive input.
 */
export function displayToMinutes(text: string, mode: TimeUnitMode): number | null {
  const trimmed = text.trim().replace(",", ".");
  if (trimmed === "") return null;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return Math.round(mode === "min" ? parsed : parsed * 60);
}
