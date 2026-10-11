import type { ReportDefinition, ReportRunIn } from "@/lib/reportsApi";
import { displayTimeZone } from "@/lib/timeZone";

/** Rows a preview asks for at most. */
export const PREVIEW_ROWS = 200;

/** The definition as the preview runs it: capped rows, and no parameter blocks the run for lack of a value. */
export function previewBody(definition: ReportDefinition): ReportRunIn {
  return {
    definition: {
      ...definition,
      limit: Math.min(definition.limit ?? PREVIEW_ROWS, PREVIEW_ROWS),
      ...(definition.parameters
        ? { parameters: definition.parameters.map((p) => ({ ...p, required: false })) }
        : {}),
    },
    time_zone: displayTimeZone(),
    compare_previous: definition.visualization?.type === "kpi",
    force_job: false,
    no_cache: false,
  };
}
