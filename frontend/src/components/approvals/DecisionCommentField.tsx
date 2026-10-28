import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { ApprovalDecision } from "@/lib/approvalsApi";
import { inputCls } from "./pickers";

/** Longest vote comment the API accepts. */
export const APPROVAL_COMMENT_MAX = 2000;

/**
 * The comment box of every decision form (agent dialog, customer portal, e-mail link page): a
 * rejection needs a reason, so the label says so until a choice is made and turns into "Begründung
 * der Ablehnung" once Ablehnen is chosen. `testIdPrefix` yields `<p>-comment`, `-comment-error`, `-counter`.
 */
export function DecisionCommentField({
  value,
  onChange,
  decision,
  showError,
  testIdPrefix,
}: {
  value: string;
  onChange: (next: string) => void;
  /** The choice made so far; null while nothing is chosen yet. */
  decision: ApprovalDecision | null;
  /** Show the "reason missing" error (only meaningful for a rejection without text). */
  showError: boolean;
  testIdPrefix: string;
}) {
  const { t } = useTranslation();
  const id = useId();
  const missing = showError && decision === "rejected" && value.trim() === "";
  const label =
    decision === "rejected"
      ? t("approvals.decision.commentRequired")
      : decision === "approved"
        ? t("approvals.decision.comment")
        : t("approvals.decision.commentUntilChosen");
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted">
        {label}
      </label>
      <textarea
        id={id}
        className={`${inputCls} w-full`}
        rows={4}
        maxLength={APPROVAL_COMMENT_MAX}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={missing}
        data-testid={`${testIdPrefix}-comment`}
      />
      <div className="mt-1 flex justify-between gap-2 text-xs">
        <span className="text-danger" role={missing ? "alert" : undefined} data-testid={`${testIdPrefix}-comment-error`}>
          {missing ? t("approvals.decision.commentMissing") : ""}
        </span>
        <span className="text-muted" data-testid={`${testIdPrefix}-counter`}>
          {t("approvals.decision.counter", { count: value.length, max: APPROVAL_COMMENT_MAX })}
        </span>
      </div>
    </div>
  );
}
