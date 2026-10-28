import { useTranslation } from "react-i18next";

/** Placeholder; the customer's list follows in the portal approvals task. */
export function PortalApprovalsPage() {
  const { t } = useTranslation();
  return (
    <div className="space-y-4" data-testid="portal-approvals-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("approvals.portal.title")}</h1>
    </div>
  );
}
