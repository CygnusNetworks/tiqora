/** Search of `/agent/reports/schedules/new`: the report to start the schedule with. */
export type ReportScheduleNewSearch = { report?: string };

export function validateScheduleNewSearch(s: Record<string, unknown>): ReportScheduleNewSearch {
  // TanStack parses a hand-typed `?report=41` (a saved report key) to the number 41.
  const raw = typeof s.report === "number" && Number.isSafeInteger(s.report) && s.report > 0 ? String(s.report) : s.report;
  return { report: typeof raw === "string" && raw.length > 0 && raw.length <= 120 ? raw : undefined };
}
