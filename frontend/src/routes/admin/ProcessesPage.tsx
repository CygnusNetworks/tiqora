import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";
import { deployStatusApi, type AdminProcess } from "@/lib/deployStatusApi";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { DeployStatusCard } from "@/components/process/DeployStatusCard";
import { Badge } from "@/components/ui/Badge";

const STATE_TONE: Record<string, "success" | "muted" | "warn"> = {
  Active: "success",
  Inactive: "muted",
  FadeAway: "warn",
};

/**
 * Read-only process list — no editing, no visual designer (see
 * ProcessDetailPage.tsx and the "read-only" note badge below). Process
 * design must be done in Znuny's own admin interface.
 *
 * ``GET /api/v1/admin/processes`` lists every process of the deployed set with its state
 * (Active / Inactive / FadeAway) and activity count, so the page needs no per-process detail
 * calls and also shows the processes agents cannot start.
 */
export function ProcessesPage() {
  const { t } = useTranslation();

  const listQ = useQuery({
    queryKey: ["admin", "processes"],
    queryFn: ({ signal }) => deployStatusApi.processes(signal),
  });

  const columns: DataTableColumn<AdminProcess>[] = [
    {
      key: "name",
      header: t("admin.processes.name"),
      render: (r) => (
        <Link
          to="/admin/processes/$processEntityId"
          params={{ processEntityId: r.entity_id }}
          className="text-accent hover:underline"
          data-testid={`process-link-${r.entity_id}`}
        >
          {r.name}
        </Link>
      ),
    },
    { key: "entity_id", header: t("admin.processes.entityId"), mono: true, render: (r) => r.entity_id },
    {
      key: "state",
      header: t("admin.processes.stateLabel"),
      render: (r) => (
        <Badge tone={STATE_TONE[r.state_name] ?? "muted"} data-testid={`process-state-${r.entity_id}`}>
          {r.state_name
            ? t(`admin.processes.states.${r.state_name}`, { defaultValue: r.state_name })
            : "—"}
        </Badge>
      ),
    },
    {
      key: "activityCount",
      header: t("admin.processes.activityCount"),
      render: (r) => <Badge tone="muted">{r.activity_count}</Badge>,
    },
  ];

  return (
    <div className="space-y-3 p-4" data-testid="admin-processes-page">
      <div className="flex items-center justify-between">
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.processes.title_plural")}
        </h1>
        <Badge tone="muted">{t("admin.readOnly")}</Badge>
      </div>
      <p className="text-sm text-muted">{t("admin.processes.readOnlyNote")}</p>
      <DeployStatusCard kind="process" />
      <DataTable
        columns={columns}
        rows={listQ.data ?? []}
        rowKey={(r) => r.entity_id}
        isLoading={listQ.isLoading}
        testId="admin-processes-table"
      />
    </div>
  );
}
