import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { approvalErrorMessage, approvalsApi, type ApprovalDetail } from "@/lib/approvalsApi";
import { AgentSelect } from "./pickers";

/** Hand an approver row to another agent; the forward call takes the new agent only (no comment). */
export function ForwardDialog({
  open,
  onClose,
  approvalId,
  approverId,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  approvalId: number;
  approverId: number;
  onDone: (detail: ApprovalDetail) => void;
}) {
  const { t } = useTranslation();
  const [userId, setUserId] = useState<number | null>(null);
  const mutation = useMutation({
    mutationFn: (id: number) => approvalsApi.forward(approvalId, approverId, { user_id: id }),
    onSuccess: (detail) => onDone(detail),
  });
  const reset = mutation.reset;
  useEffect(() => {
    if (!open) return;
    setUserId(null);
    reset();
  }, [open, reset]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("approvals.forward.title")}
      description={t("approvals.forward.description")}
      footer={
        <>
          <Button onClick={onClose} data-testid="forward-cancel">
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            disabled={userId === null || mutation.isPending}
            onClick={() => userId !== null && mutation.mutate(userId)}
            data-testid="forward-submit"
          >
            {t("approvals.forward.submit")}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <AgentSelect value={userId} onChange={setUserId} testId="forward-agent" ariaLabel={t("approvals.forward.agent")} />
        {mutation.isError && (
          <p className="text-xs text-danger" role="alert" data-testid="forward-error">
            {approvalErrorMessage(mutation.error, t)}
          </p>
        )}
      </div>
    </Dialog>
  );
}
