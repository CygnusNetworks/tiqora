import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";

/** Shows the reports only to agents the server lets see them (`can_view_reports`); everyone else gets a plain notice. */
export function ReportsAccessGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  if (user?.can_view_reports) return <>{children}</>;
  return (
    <div className="m-6 rounded-lg border border-hairline bg-surface p-8 text-center" data-testid="reports-denied">
      <h1 className="font-display text-lg font-semibold text-ink">{t("reports.deniedTitle")}</h1>
      <p className="mt-2 text-sm text-muted">{t("reports.deniedBody")}</p>
    </div>
  );
}
