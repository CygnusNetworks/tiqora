import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";
import { api, type ProcessSummaryOut } from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { Badge } from "@/components/ui/Badge";

type Row = ProcessSummaryOut & { activityCount: number };

/**
 * Read-only process list — no editing, no visual designer (see
 * ProcessDetailPage.tsx and the "read-only" note badge below). Process
 * design must be done in Znuny's own admin interface.
 *
 * ``ProcessSummaryOut`` (``GET /api/v1/process/``) doesn't carry an
 * activity count, so this fetches each process's detail in parallel to
 * derive one — acceptable here since process counts are admin-page-scale
 * (a handful, not thousands), unlike the paginated ticket-list endpoints.
 */
export function ProcessesPage() {
  const { t } = useTranslation();

  const listQ = useQuery({
    queryKey: ["admin", "processes"],
    queryFn: async ({ signal }): Promise<Row[]> => {
      const summaries = await api.listProcesses(signal);
      const details = await Promise.all(
        summaries.map((s) => api.getProcess(s.entity_id, signal)),
      );
      return summaries.map((s, i) => ({ ...s, activityCount: details[i].activities.length }));
    },
  });

  const columns: DataTableColumn<Row>[] = [
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
      key: "activityCount",
      header: t("admin.processes.activityCount"),
      render: (r) => <Badge tone="muted">{r.activityCount}</Badge>,
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
