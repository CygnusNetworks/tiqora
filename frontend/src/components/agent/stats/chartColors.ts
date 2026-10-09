/** Server key of the folded-away tail ("Weitere"); always neutral, never a slot (same value as reportFormat). */
export const OTHER_SERIES_KEY = "__other__";

/** Number of categorical colour slots; everything beyond folds into "Weitere" (muted). */
export const SERIES_SLOTS = 6;

/** `var(--color-series-N)` for slot 0..5; later slots are the muted "Weitere" colour. */
export function seriesColor(slot: number): string {
  if (!Number.isInteger(slot) || slot < 0 || slot >= SERIES_SLOTS) return "var(--color-muted)";
  return `var(--color-series-${slot + 1})`;
}

function compareKeys(a: string, b: string): number {
  const na = Number(a);
  const nb = Number(b);
  if (a.trim() !== "" && b.trim() !== "" && Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
  return a < b ? -1 : a > b ? 1 : 0;
}

/**
 * Slot per series key. Enum-valued series pass `fixedOrder` (their slot is their
 * position in it), so filtering never repaints them. Id-valued series (queues, agents)
 * are ordered by key (ids numerically), never by value rank. Limitation: a filter
 * that removes a lower id shifts the colours of later ids.
 */
export function assignSeriesSlots(
  keys: readonly string[],
  fixedOrder?: readonly string[],
): Map<string, number> {
  const slots = new Map<string, number>();
  const fixed = fixedOrder ?? [];
  const rest: string[] = [];
  for (const key of keys) {
    if (key === OTHER_SERIES_KEY) continue;
    const at = fixed.indexOf(key);
    if (at >= 0) slots.set(key, at);
    else if (!rest.includes(key)) rest.push(key);
  }
  rest.sort(compareKeys);
  let next = fixed.length;
  for (const key of rest) slots.set(key, fixedOrder ? next++ : slots.size);
  return slots;
}

/** Colour of a series key: `__other__` is always the neutral muted token, others follow their slot. */
export function keyColor(key: string, slots: ReadonlyMap<string, number>): string {
  if (key === OTHER_SERIES_KEY) return "var(--color-muted)";
  return seriesColor(slots.get(key) ?? SERIES_SLOTS);
}

/** Ramp step 0..6 for a 0..1 value. */
export function sequentialStep(t: number): number {
  const clamped = Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0;
  return Math.min(6, Math.floor(clamped * 7));
}

/** 0..1 -> `var(--color-seq-1..7)` (heatmap ramp). */
export function sequentialColor(t: number): string {
  return `var(--color-seq-${sequentialStep(t) + 1})`;
}

export const CHANNEL_ORDER = ["email", "phone", "telegram", "webchat", "sms", "whatsapp", "internal"] as const;
export const STATE_TYPE_ORDER = [
  "new",
  "open",
  "pending reminder",
  "pending auto",
  "closed",
  "merged",
  "removed",
] as const;

/** Fixed text colours per ramp step: the ramp is the same in light and dark mode, so the text must not flip with the theme. */
export function valueTextColor(tone: number): string {
  return sequentialStep(tone) <= 3 ? "#0d1b2e" : "#ffffff";
}
