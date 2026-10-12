import type { ReportResult } from "@/lib/reportsApi";

/** `{ rate in percent, tickets }` of a one-row SLA result (ratio 0..1 -> percent); the same normalisation as the provider. */
export function gaugeFigures(result: ReportResult): { rate: number | null; tickets: number } {
  const row = result.rows[0];
  if (!row) return { rate: null, tickets: 0 };
  const keys = result.columns.map((c) => c.key);
  const rate = row[keys.indexOf("sla_met_rate")];
  const tickets = row[keys.indexOf("tickets")];
  return {
    rate: typeof rate === "number" ? Math.round(rate * 1000) / 10 : null,
    tickets: typeof tickets === "number" ? tickets : 0,
  };
}
