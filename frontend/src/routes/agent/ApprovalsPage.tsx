import { useTranslation } from "react-i18next";

/** Placeholder; the inbox, side panel and delegation tab follow in the approvals UI tasks. */
export function ApprovalsPage() {
  const { t } = useTranslation();
  return (
    <div className="space-y-4 p-6" data-testid="approvals-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("approvals.agent.title")}</h1>
    </div>
  );
}
