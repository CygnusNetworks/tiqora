import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "@tanstack/react-router";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";

export function GenericAgentJobDetailPage() {
  const { t } = useTranslation();
  const { jobName } = useParams({ from: "/admin/generic-agent-jobs/$jobName" });
  const detailQ = useQuery({
    queryKey: ["admin", "generic-agent-jobs", jobName],
    queryFn: ({ signal }) => api.getGenericAgentJob(jobName, signal),
  });

  return (
    <div className="space-y-3 p-4" data-testid="admin-generic-agent-job-detail-page">
      <Link to="/admin/generic-agent-jobs" className="text-sm text-accent hover:underline">
        {t("common.back")}
      </Link>
      <h1 className="font-display text-xl font-semibold text-ink">{jobName}</h1>
      {detailQ.isLoading ? (
        <Spinner />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
          <table className="w-full min-w-[480px] border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-hairline bg-surface-subtle text-xs uppercase tracking-wide text-muted">
                <th className="py-1.5 pl-4 pr-2 font-medium">{t("admin.genericAgentJobs.key")}</th>
                <th className="py-1.5 pr-4 font-medium">{t("admin.genericAgentJobs.value")}</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(detailQ.data?.settings ?? {}).map(([key, value]) => (
                <tr key={key} className="border-b border-hairline last:border-b-0">
                  <td className="py-1 pl-4 pr-2 font-mono text-xs">{key}</td>
                  <td className="py-1 pr-4 text-xs">{value ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
