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
