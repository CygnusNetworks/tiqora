/** Ticket priority display helpers.
 *
 * Znuny priorities carry a leading numeric rank in their name (e.g.
 * "3 normal", "5 very high"). That rank is an internal ordering device, not
 * something agents read as part of the label, so the UI shows the bare name.
 */

/** Drop Znuny's leading numeric rank ("3 normal" → "normal"). */
export function priorityName(priority: string | null | undefined): string | null {
  if (!priority) return null;
  return priority.replace(/^\s*\d+\s+/, "");
}

/** Stable i18n key segment under `ticket.priorityName.*` for a stock Znuny priority. */
export type PriorityNameKey = "veryLow" | "low" | "normal" | "high" | "veryHigh";

const PRIORITY_NAME_KEYS: Record<string, PriorityNameKey> = {
  "very low": "veryLow",
  low: "low",
  normal: "normal",
  high: "high",
  "very high": "veryHigh",
};

/**
 * Localised display label for a Znuny priority name, rank dropped
 * ("5 very high" → "sehr hoch"). Custom priority names keep their bare name;
 * an empty priority gives `fallback`.
 *
 * `t` is the react-i18next `t` function (or any compatible translator).
 */
export function priorityLabel(
  t: (key: string, options?: { defaultValue?: string }) => string,
  priority: string | null | undefined,
  fallback = "—",
): string {
  const bare = priorityName(priority);
  if (!bare) return fallback;
  const key = PRIORITY_NAME_KEYS[bare.toLowerCase().trim()];
  return key ? t(`ticket.priorityName.${key}`, { defaultValue: bare }) : bare;
}

/** Extract the leading numeric rank from a Znuny priority name ("5 very high" → 5). */
export function priorityIdFromName(priority: string | null | undefined): number | null {
  if (!priority) return null;
  const m = priority.match(/^\s*(\d+)\s+/);
  if (!m) return null;
  const n = Number(m[1]);
  return Number.isFinite(n) ? n : null;
}

/**
 * CSS colour variable for a Znuny priority id (1=lowest … 5=highest).
 * Clamps out-of-range ids into 1..5; null/unknown → neutral mid-ramp (3).
 */
export function priorityColorVar(priorityId: number | null | undefined): string {
  if (priorityId == null || !Number.isFinite(priorityId)) {
    return "var(--color-prio-3)";
  }
  const n = Math.min(5, Math.max(1, Math.round(priorityId)));
  return `var(--color-prio-${n})`;
}

/** @deprecated Prefer soft-chip colour via `priorityColorVar`. Kept for any residual callers. */
export function priorityTextClass(priorityId: number | null | undefined): string {
  if (priorityId == null) return "text-ink";
  if (priorityId >= 5) return "text-danger";
  if (priorityId === 4) return "text-warn";
  return "text-ink";
}
