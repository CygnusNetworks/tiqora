import type { TFunction } from "i18next";
import { approvalErrorCode, approvalErrorMessage } from "@/lib/approvalsApi";

/** `<input type="datetime-local">` value to an ISO instant, or null when empty/invalid. */
export function toIso(local: string): string | null {
  if (!local) return null;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export type PeriodProblem = "untilPast" | "untilBeforeFrom";

/** What is wrong with a substitute period, if anything (`from` empty means "now"). */
export function periodProblem(fromIso: string | null, untilIso: string | null): PeriodProblem | null {
  if (!untilIso) return null;
  if (new Date(untilIso).getTime() <= Date.now()) return "untilPast";
  if (fromIso && new Date(untilIso) <= new Date(fromIso)) return "untilBeforeFrom";
  return null;
}

/** Text of a failed create. A period that ended already is answered 422 `invalid_definition`; it can only come from valid_until. */
export function delegationCreateMessage(error: unknown, untilIso: string | null, t: TFunction): string | null {
  if (!error) return null;
  return approvalErrorCode(error) === "invalid_definition" && untilIso
    ? t("approvals.agent.delegation.untilPast")
    : approvalErrorMessage(error, t);
}
