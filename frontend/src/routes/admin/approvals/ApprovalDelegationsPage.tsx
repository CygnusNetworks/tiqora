import { useTranslation } from "react-i18next";

/** Placeholder; filled by the admin approvals UI tasks. */
export function ApprovalDelegationsPage() {
  const { t } = useTranslation();
  return (
    <div className="space-y-4" data-testid="admin-approval-delegations-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("admin.nav.approvalsDelegations")}</h1>
    </div>
  );
}
