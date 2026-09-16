/**
 * Last-used refine tone, remembered per browser so an agent does not reset it
 * on every reply. Same shape as `loadTimeUnitMode` in `./timeUnits` — the
 * choice is a display preference, not ticket state, so it never leaves the
 * browser.
 */
import { REFINE_TONES, type RefineTone } from "./refineApi";

const STORAGE_KEY = "tiqora-refine-tone";

export function loadRefineTone(): RefineTone {
  if (typeof window === "undefined") return "standard";
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return REFINE_TONES.includes(stored as RefineTone)
      ? (stored as RefineTone)
      : "standard";
  } catch {
    return "standard";
  }
}

export function saveRefineTone(tone: RefineTone): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, tone);
  } catch {
    // private mode / SSR — ignore
  }
}
