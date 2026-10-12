import type { TFunction } from "i18next";
import type { ValidationIssue } from "@/lib/processDesignerApi";

/** Text of one issue from its code and parameters; an unknown code falls back to the code itself. */
export function issueText(t: TFunction, issue: ValidationIssue): string {
  const { count: _count, context: _context, ...params } = issue.params as Record<string, string>;
  // `reason` is a backend code (regexp, name, possible_values), never shown raw.
  if (params.reason) {
    params.reason = t(`admin.processDesigner.validation.reason.${params.reason}`, { defaultValue: params.reason });
  }
  return t(`admin.processDesigner.validation.code.${issue.code}`, { ...params, defaultValue: issue.code });
}
