/** Pure helpers behind the queue dialog's escalation matrix. */

export type DurationUnits = { day: string; hour: string; minute: string };

/** 150 → "2 Std 30 Min", 1440 → "1 T"; empty for 0, negatives and junk. */
export function humanizeMinutes(minutes: number, units: DurationUnits): string {
  if (!Number.isFinite(minutes) || minutes <= 0) return "";
  const m = Math.round(minutes);
  const d = Math.floor(m / 1440);
  const h = Math.floor((m % 1440) / 60);
  const r = m % 60;
  const parts: string[] = [];
  if (d) parts.push(`${d} ${units.day}`);
  if (h) parts.push(`${h} ${units.hour}`);
  if (r) parts.push(`${r} ${units.minute}`);
  return parts.join(" ");
}

/** Form value ("" / null / number / numeric string) → minutes, 0 when unset. */
export function toMinutes(v: unknown): number {
  if (v === "" || v === null || v === undefined) return 0;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export const ESCALATION_STAGES = [
  { key: "firstResponse", time: "first_response_time", notify: "first_response_notify" },
  { key: "update", time: "update_time", notify: "update_notify" },
  { key: "solution", time: "solution_time", notify: "solution_notify" },
] as const;

/** How many of the three stages have a time set (> 0 minutes). */
export function activeEscalationStages(values: Record<string, unknown>): number {
  return ESCALATION_STAGES.filter((s) => toMinutes(values[s.time]) > 0).length;
}
