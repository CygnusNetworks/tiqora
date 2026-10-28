import { useEffect, useId, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import {
  approvalErrorMessage,
  approvalsApi,
  type ApprovalDecision,
  type ApprovalDetail,
} from "@/lib/approvalsApi";
import { inputCls } from "./pickers";

/** Longest vote comment the API accepts. */
export const APPROVAL_COMMENT_MAX = 2000;

/** Approve or reject a request; a rejection needs a comment. `approverId` votes on behalf of a delegator. */
export function DecisionDialog({
  open,
  onClose,
  approvalId,
  approverId,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  approvalId: number;
  approverId?: number;
  onDone: (detail: ApprovalDetail) => void;
}) {
  const { t } = useTranslation();
  const fieldId = useId();
  const [decision, setDecision] = useState<ApprovalDecision>("approved");
  const [comment, setComment] = useState("");
  const [touched, setTouched] = useState(false);

  const mutation = useMutation({
    mutationFn: () =>
      approvalsApi.decide(approvalId, {
        decision,
        comment: comment.trim() || null,
        approver_id: approverId ?? null,
      }),
    onSuccess: (detail) => onDone(detail),
  });
  const reset = mutation.reset;
  useEffect(() => {
    if (!open) return;
    setDecision("approved");
    setComment("");
    setTouched(false);
    reset();
  }, [open, reset]);

  const missing = decision === "rejected" && comment.trim() === "";
  const submit = () => {
    setTouched(true);
    if (missing || mutation.isPending) return;
    mutation.mutate();
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("approvals.decision.title")}
      footer={
        <>
          <Button onClick={onClose} data-testid="decision-cancel">
            {t("common.cancel")}
          </Button>
          <Button
            variant={decision === "rejected" ? "danger" : "primary"}
            onClick={submit}
            disabled={mutation.isPending}
            data-testid="decision-submit"
          >
            {decision === "rejected" ? t("approvals.decision.reject") : t("approvals.decision.approve")}
          </Button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <SegmentedControl<ApprovalDecision>
          items={[
            { value: "approved", label: t("approvals.decision.approve") },
            { value: "rejected", label: t("approvals.decision.reject") },
          ]}
          value={decision}
          onChange={setDecision}
          testId="decision-choice"
          aria-label={t("approvals.decision.title")}
          fullWidth
        />
        <div>
          <label htmlFor={fieldId} className="mb-1 block text-xs font-medium text-muted">
            {decision === "rejected" ? t("approvals.decision.commentRequired") : t("approvals.decision.comment")}
          </label>
          <textarea
            id={fieldId}
            className={`${inputCls} w-full`}
            rows={4}
            maxLength={APPROVAL_COMMENT_MAX}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            aria-invalid={touched && missing}
            data-testid="decision-comment"
          />
          <div className="mt-1 flex justify-between gap-2 text-xs">
            <span className="text-danger" role={touched && missing ? "alert" : undefined} data-testid="decision-comment-error">
              {touched && missing ? t("approvals.decision.commentMissing") : ""}
            </span>
            <span className="text-muted" data-testid="decision-counter">
              {comment.length} / {APPROVAL_COMMENT_MAX}
            </span>
          </div>
        </div>
        {mutation.isError && (
          <p className="text-xs text-danger" role="alert" data-testid="decision-error">
            {approvalErrorMessage(mutation.error, t)}
          </p>
        )}
      </form>
    </Dialog>
  );
}
