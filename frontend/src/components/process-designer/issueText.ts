import type { TFunction } from "i18next";
import type { ValidationIssue } from "@/lib/processDesignerApi";

/** Text of one issue from its code and parameters; an unknown code falls back to the code itself. */
export function issueText(t: TFunction, issue: ValidationIssue): string {
  const { count: _count, context: _context, ...params } = issue.params as Record<string, string>;
  return t(`admin.processDesigner.validation.code.${issue.code}`, { ...params, defaultValue: issue.code });
}
