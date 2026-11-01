import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useApprovalsStatus } from "@/components/approvals/useApprovalsEnabled";
import { RouterAnchor } from "@/components/ui/RouterAnchor";

/** Title of an admin approvals page, its action and the link to the generic approvals settings. */
export function AdminApprovalsHeader({ title, action }: { title: string; action?: ReactNode }) {
  const { t } = useTranslation();
  const off = useApprovalsStatus().data?.enabled === false;
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="font-display text-xl font-semibold text-ink">{title}</h1>
        <div className="flex flex-wrap items-center gap-3">
          <RouterAnchor
            href="/admin/settings/approvals"
            className="text-sm text-accent hover:underline"
            data-testid="admin-approvals-settings-link"
          >
            {t("approvals.admin.settings")}
          </RouterAnchor>
          {action}
        </div>
      </div>
      {off && (
        <p className="rounded-md border border-amber/30 bg-amber/10 px-3 py-2 text-sm text-ink" role="status" data-testid="admin-approvals-off">
          {t("approvals.admin.moduleOff")}
        </p>
      )}
    </>
  );
}
