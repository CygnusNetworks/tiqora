import { useEffect, useState } from "react";
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
import { DecisionCommentField } from "./DecisionCommentField";

export { APPROVAL_COMMENT_MAX } from "./DecisionCommentField";

/** Approve or reject a request; a rejection needs a comment. `approverId` votes on behalf of a delegator. */
export function DecisionDialog({
  open,
  onClose,
  approvalId,
  approverId,
  initialDecision = "approved",
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  approvalId: number;
  approverId?: number;
  /** Choice the dialog opens with (the inbox has one button per choice). */
  initialDecision?: ApprovalDecision;
  onDone: (detail: ApprovalDetail) => void;
}) {
  const { t } = useTranslation();
  const [decision, setDecision] = useState<ApprovalDecision>(initialDecision);
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
    setDecision(initialDecision);
    setComment("");
    setTouched(false);
    reset();
  }, [open, reset, initialDecision]);

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
        <DecisionCommentField value={comment} onChange={setComment} decision={decision} showError={touched} testIdPrefix="decision" />
        {mutation.isError && (
          <p className="text-xs text-danger" role="alert" data-testid="decision-error">
            {approvalErrorMessage(mutation.error, t)}
          </p>
        )}
      </form>
    </Dialog>
  );
}
