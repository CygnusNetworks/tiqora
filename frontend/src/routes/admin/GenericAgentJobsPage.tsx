import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate } from "@tanstack/react-router";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { actionCount, criteriaCount, decodeJob, scheduleSummary } from "@/lib/genericAgentJob";

export function GenericAgentJobsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const listQ = useQuery({
    queryKey: ["admin", "generic-agent-jobs"],
    queryFn: ({ signal }) => api.listGenericAgentJobs(signal),
  });

  const weekdays = t("admin.genericAgentJobs.weekdaysShort", {
    returnObjects: true,
  }) as string[];
  const scheduleLabels = {
    daily: t("admin.genericAgentJobs.daily"),
    hourly: t("admin.genericAgentJobs.hourly"),
    every: t("admin.genericAgentJobs.every"),
  };

  const openJob = (jobName: string) =>
    void navigate({ to: "/admin/generic-agent-jobs/$jobName", params: { jobName } });

  const renderRow = (jobName: string, settings: Record<string, string[]>) => {
    const job = decodeJob(settings);
    const schedule = scheduleSummary(job, weekdays, scheduleLabels);
    return (
      <div key={jobName} className="border-t border-hairline first:border-t-0">
        <div
          role="button"
          tabIndex={0}
          data-testid={`generic-agent-job-row-${jobName}`}
          onClick={() => openJob(jobName)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              openJob(jobName);
            }
          }}
          className="grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 px-3.5 py-2.5 transition-colors hover:bg-surface-subtle"
        >
          <div className="flex min-w-0 items-center gap-2.5">
            <span
              className={cn(
                "h-1.5 w-1.5 shrink-0 rounded-full",
                job.valid && job.hasSchedule ? "bg-green" : "bg-muted",
              )}
              title={
                job.valid && job.hasSchedule
                  ? t("admin.genericAgentJobs.active")
                  : t("admin.genericAgentJobs.inactive")
              }
            />
            <span className="truncate text-sm font-medium text-ink">{jobName}</span>
            <span
              className={cn(
                "shrink-0 text-xs",
                schedule ? "text-muted" : "italic text-muted",
              )}
            >
              {schedule ?? t("admin.genericAgentJobs.manual")}
            </span>
          </div>
          <div className="col-start-1 row-start-2 flex min-w-0 items-baseline gap-3 pl-4">
            <span className="truncate font-mono text-xs text-muted">
              {t("admin.genericAgentJobs.countSummary", {
                criteria: criteriaCount(job),
                actions: actionCount(job),
              })}
            </span>
          </div>
          <div className="row-span-2 flex items-center justify-end">
            <Badge tone={job.valid ? "success" : "muted"}>
              {job.valid
                ? t("admin.genericAgentJobs.active")
                : t("admin.genericAgentJobs.inactive")}
            </Badge>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-3 p-4" data-testid="admin-generic-agent-jobs-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">
            {t("admin.genericAgentJobs.title_plural")}
          </h1>
          <p className="mt-1 text-xs text-muted">{t("admin.genericAgentJobs.description")}</p>
        </div>
        <Badge tone="muted">{t("admin.readOnly")}</Badge>
      </div>
      <div
        className="rounded-xl border border-hairline bg-surface"
        data-testid="admin-generic-agent-jobs-table"
      >
        {listQ.isLoading ? (
          <div className="flex justify-center p-6">
            <Spinner />
          </div>
        ) : (listQ.data?.length ?? 0) === 0 ? (
          <p className="p-4 text-sm text-muted">{t("admin.table.empty")}</p>
        ) : (
          (listQ.data ?? []).map((j) => renderRow(j.job_name, j.settings))
        )}
      </div>
    </div>
  );
}
