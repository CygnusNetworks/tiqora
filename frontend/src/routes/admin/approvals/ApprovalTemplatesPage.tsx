import { useTranslation } from "react-i18next";

/** Placeholder; filled by the admin approvals UI tasks. */
export function ApprovalTemplatesPage() {
  const { t } = useTranslation();
  return (
    <div className="space-y-4" data-testid="admin-approval-templates-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("admin.nav.approvalsTemplates")}</h1>
    </div>
  );
}
