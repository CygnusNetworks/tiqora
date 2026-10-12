import type { ReportResult } from "@/lib/reportsApi";

/** The background job's own result is uncapped: cut it like the server cuts a served one (`row_cap`). */
export function capJobRows(result: ReportResult, cap: number | undefined): ReportResult {
  if (cap === undefined || result.rows.length <= cap) return result;
  return { ...result, rows: result.rows.slice(0, cap), truncated: true };
}
