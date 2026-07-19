import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";
import { api } from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { Badge } from "@/components/ui/Badge";

type Row = { job_name: string; settingCount: number };

export function GenericAgentJobsPage() {
  const { t } = useTranslation();
  const listQ = useQuery({
    queryKey: ["admin", "generic-agent-jobs"],
    queryFn: ({ signal }) => api.listGenericAgentJobs(signal),
  });

  const rows: Row[] = (listQ.data ?? []).map((j) => ({
    job_name: j.job_name,
    settingCount: Object.keys(j.settings).length,
  }));

  const columns: DataTableColumn<Row>[] = [
    {
      key: "job_name",
      header: t("admin.genericAgentJobs.name"),
      render: (r) => (
        <Link
          to="/admin/generic-agent-jobs/$jobName"
          params={{ jobName: r.job_name }}
          className="text-accent hover:underline"
          data-testid={`generic-agent-job-link-${r.job_name}`}
        >
          {r.job_name}
        </Link>
      ),
    },
    {
      key: "settings",
      header: t("admin.genericAgentJobs.settings"),
      render: (r) => <Badge tone="muted">{r.settingCount}</Badge>,
    },
  ];

  return (
    <div className="space-y-3 p-4" data-testid="admin-generic-agent-jobs-page">
      <div className="flex items-center justify-between">
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.genericAgentJobs.title_plural")}
        </h1>
        <Badge tone="muted">{t("admin.readOnly")}</Badge>
      </div>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.job_name}
        isLoading={listQ.isLoading}
        testId="admin-generic-agent-jobs-table"
      />
    </div>
  );
}
