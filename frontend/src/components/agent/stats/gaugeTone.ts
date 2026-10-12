/** Within this many points below the target the gauge is "warn" instead of "danger". */
export const GAUGE_WARN_MARGIN = 5;

export type GaugeTone = "success" | "warn" | "danger" | "neutral";

/** `success` at or above the target, `warn` within 5 points below it, `danger` below that. */
export function gaugeTone(value: number, target?: number): GaugeTone {
  if (target === undefined) return "neutral";
  if (value >= target) return "success";
  if (value >= target - GAUGE_WARN_MARGIN) return "warn";
  return "danger";
}
