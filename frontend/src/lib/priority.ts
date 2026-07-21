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
