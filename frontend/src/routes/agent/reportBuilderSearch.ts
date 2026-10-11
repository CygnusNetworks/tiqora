export type ReportBuilderSearch = { from?: string };

/**
 * Router `validateSearch` of `/agent/reports/new`. The router parses search values as JSON, so an
 * address typed as `?from=41` arrives as the number 41: both spellings name the saved report "41".
 */
export function validateReportBuilderSearch(s: Record<string, unknown>): ReportBuilderSearch {
  const raw = typeof s.from === "number" && Number.isSafeInteger(s.from) ? String(s.from) : s.from;
  return { from: typeof raw === "string" && /^[A-Za-z0-9_.-]{1,120}$/.test(raw) ? raw : undefined };
}
