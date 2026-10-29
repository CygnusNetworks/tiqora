import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { approvalErrorMessage } from "@/lib/approvalsApi";

/**
 * "Anforderung zurückziehen?" for the inbox, the ticket panel and the admin overview. A withdrawal is final
 * (the approvers' links die and nobody can decide any more), so every page asks first.
 */
export function WithdrawConfirm({
  open,
  title,
  pending,
  error,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  /** Title of the request being withdrawn. */
  title: string;
  pending: boolean;
  /** A failed withdrawal, shown in the dialog. */
  error: unknown;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <ConfirmDialog
      open={open}
      title={t("approvals.withdraw.title")}
      message={
        <>
          <p>{t("approvals.withdraw.message", { title })}</p>
          {error != null && (
            <p className="mt-2 text-danger" role="alert" data-testid="withdraw-error">
              {approvalErrorMessage(error, t)}
            </p>
          )}
        </>
      }
      variant="danger"
      confirmLabel={t("approvals.withdraw.action")}
      pending={pending}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
