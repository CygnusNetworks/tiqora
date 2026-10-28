import { useTranslation } from "react-i18next";

/** Placeholder; filled by the admin approvals UI tasks. */
export function ApprovalListsPage() {
  const { t } = useTranslation();
  return (
    <div className="space-y-4" data-testid="admin-approval-lists-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("admin.nav.approvalsLists")}</h1>
    </div>
  );
}
