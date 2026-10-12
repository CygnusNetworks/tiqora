import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { DashboardConflictError, dashboardKeys, dashboardsApi, type Dashboard } from "@/lib/dashboardsApi";

const BUTTON =
  "rounded-lg px-3 py-1.5 text-xs transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50";

/** The server's own text of a 422 (`{detail: {message, path}}`), if it sent one. */
function serverMessage(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null;
  const body = err.detail as { detail?: unknown } | null;
  const inner = (body && typeof body === "object" && "detail" in body ? body.detail : body) as { message?: unknown } | null;
  return inner && typeof inner === "object" && typeof inner.message === "string" ? inner.message : null;
}

/**
 * "Neue Version der Vorlage …": offered above the grid to the owner of a dashboard copied from a
 * role template whose template gained widgets. "Übernehmen" appends them, "Ausblenden" hides the
 * offer until the template changes again. Both post the dashboard's `version`.
 */
export function TemplateUpdateBanner({
  dashboard,
  editing = false,
}: {
  dashboard: Pick<Dashboard, "id" | "version" | "can_edit" | "template_update">;
  editing?: boolean;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const hint = dashboard.template_update;

  const apply = useMutation({
    mutationFn: (action: "merge" | "dismiss") =>
      dashboardsApi.applyTemplateUpdate(dashboard.id, { action, version: dashboard.version }),
    onSuccess: (saved) => {
      setMessage(null);
      qc.setQueryData(dashboardKeys.one(dashboard.id), saved);
      void qc.invalidateQueries({ queryKey: dashboardKeys.data(dashboard.id) });
      void qc.invalidateQueries({ queryKey: dashboardKeys.list() });
    },
    onError: (err) => {
      const conflict = err instanceof DashboardConflictError || (err instanceof ApiError && err.status === 404);
      setMessage(conflict ? t("dashboards.view.updateConflict") : (serverMessage(err) ?? t("dashboards.view.updateFailed")));
      if (conflict) {
        void qc.invalidateQueries({ queryKey: dashboardKeys.one(dashboard.id) });
        void qc.invalidateQueries({ queryKey: dashboardKeys.data(dashboard.id) });
        void qc.invalidateQueries({ queryKey: dashboardKeys.list() });
      }
    },
  });

  if (editing || !dashboard.can_edit || !hint) return null;

  return (
    <div
      role="status"
      className="flex flex-wrap items-center justify-between gap-3 rounded-[14px] border border-accent/30 bg-accent/10 px-4 py-2.5 text-sm text-ink"
    >
      <div className="min-w-0">
        <p>{t("dashboards.view.updateBanner", { name: hint.template_name, count: hint.new_widget_count })}</p>
        {message && <p className="mt-1 text-danger">{message}</p>}
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={apply.isPending}
          onClick={() => apply.mutate("merge")}
          className={cn(BUTTON, "bg-accent font-semibold text-accent-ink hover:bg-accent/90")}
        >
          {t("dashboards.view.updateMerge")}
        </button>
        <button
          type="button"
          disabled={apply.isPending}
          onClick={() => apply.mutate("dismiss")}
          className={cn(BUTTON, "border border-hairline bg-surface text-ink hover:bg-surface-subtle")}
        >
          {t("dashboards.view.updateDismiss")}
        </button>
      </div>
    </div>
  );
}
