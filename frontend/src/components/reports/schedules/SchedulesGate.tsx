import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { ReportsAccessGate } from "@/components/reports/ReportsAccessGate";

/** Report access plus the right to create reports (the rule for schedules, D8); everyone else gets a plain notice. */
export function SchedulesGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  return (
    <ReportsAccessGate>
      {user?.can_create_reports ? (
        children
      ) : (
        <div className="m-6 rounded-lg border border-hairline bg-surface p-8 text-center" data-testid="schedules-denied">
          <h1 className="font-display text-lg font-semibold text-ink">{t("reports.deniedTitle")}</h1>
          <p className="mt-2 text-sm text-muted">{t("reports.schedules.needsCreate")}</p>
        </div>
      )}
    </ReportsAccessGate>
  );
}
