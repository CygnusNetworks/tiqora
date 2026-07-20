import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "@tanstack/react-router";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";

/**
 * Read-only process detail: activities + which activity dialogs exist at
 * each. Transitions/conditions are intentionally not shown — the REST API
 * (``ProcessDetailOut``) doesn't expose them either, by design (see
 * ``tiqora.process.schemas.ProcessDetailOut`` docstring: Tiqora does not
 * leak internal BPM routing logic to clients). No editing, no visual
 * designer — process design must be done in Znuny's own admin interface.
 */
export function ProcessDetailPage() {
  const { t } = useTranslation();
  const { processEntityId } = useParams({ from: "/admin/processes/$processEntityId" });
  const detailQ = useQuery({
    queryKey: ["admin", "processes", processEntityId],
    queryFn: ({ signal }) => api.getProcess(processEntityId, signal),
  });

  return (
    <div className="space-y-3 p-4" data-testid="admin-process-detail-page">
      <Link to="/admin/processes" className="text-sm text-accent hover:underline">
        {t("common.back")}
      </Link>

      {detailQ.isLoading ? (
        <Spinner />
      ) : detailQ.isError || !detailQ.data ? (
        <p className="text-sm text-danger">{t("admin.processes.loadError")}</p>
      ) : (
        <>
          <div className="flex items-center justify-between">
            <h1 className="font-display text-xl font-semibold text-ink">{detailQ.data.name}</h1>
            <Badge tone="muted">{t("admin.readOnly")}</Badge>
          </div>
          <p className="text-sm text-muted">{t("admin.processes.readOnlyNote")}</p>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-xs uppercase tracking-wide text-muted">
                {t("admin.processes.entityId")}
              </dt>
              <dd className="font-mono text-ink">{detailQ.data.entity_id}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-muted">
                {t("admin.processes.startActivity")}
              </dt>
              <dd className="text-ink">{detailQ.data.start_activity_entity_id ?? "—"}</dd>
            </div>
          </dl>

          <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
            <table className="w-full min-w-[480px] border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-hairline bg-surface-subtle text-xs uppercase tracking-wide text-muted">
                  <th className="py-1.5 pl-4 pr-2 font-medium">{t("admin.processes.activities")}</th>
                  <th className="py-1.5 pr-4 font-medium">{t("admin.table.id")}</th>
                  <th className="py-1.5 pr-4 font-medium">{t("admin.processes.dialogs")}</th>
                </tr>
              </thead>
              <tbody>
                {detailQ.data.activities.map((a) => (
                  <tr key={a.entity_id} className="border-b border-hairline last:border-b-0">
                    <td className="py-1.5 pl-4 pr-2 text-ink" data-testid={`process-activity-${a.entity_id}`}>
                      {a.name}
                      {a.entity_id === detailQ.data.start_activity_entity_id && (
                        <Badge tone="accent" className="ml-2">
                          {t("admin.processes.startActivity")}
                        </Badge>
                      )}
                    </td>
                    <td className="py-1.5 pr-4 font-mono text-xs text-muted">{a.entity_id}</td>
                    <td className="py-1.5 pr-4 text-ink">
                      {a.activity_dialogs.length === 0
                        ? t("admin.processes.noDialogs")
                        : a.activity_dialogs.map((d) => d.name).join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
