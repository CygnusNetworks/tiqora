import { useTranslation } from "react-i18next";

/** Placeholder; the customer's decision view follows in the portal approvals task. */
export function PortalApprovalPage() {
  const { t } = useTranslation();
  return (
    <div className="space-y-4" data-testid="portal-approval-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("approvals.portal.title")}</h1>
    </div>
  );
}
