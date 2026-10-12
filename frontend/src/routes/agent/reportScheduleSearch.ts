/** Search of `/agent/reports/schedules/new`: the report to start the schedule with. */
export type ReportScheduleNewSearch = { report?: string };

export function validateScheduleNewSearch(s: Record<string, unknown>): ReportScheduleNewSearch {
  return { report: typeof s.report === "string" && s.report.length > 0 && s.report.length <= 120 ? s.report : undefined };
}
