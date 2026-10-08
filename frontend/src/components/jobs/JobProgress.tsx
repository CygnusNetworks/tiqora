import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { TERMINAL_JOB_STATUSES, jobsApi, type Job } from "@/lib/jobsApi";

export function JobProgress({
  job,
  onCancel,
  cancelling,
  testId = "job-progress",
}: {
  job: Job;
  onCancel?: () => void;
  cancelling?: boolean;
  testId?: string;
}) {
  const { t } = useTranslation();
  const active = !TERMINAL_JOB_STATUSES.has(job.status);
  const total = job.progress_total;
  const determinate = total != null && total > 0;
  const percent = determinate ? Math.min(100, Math.round((job.progress_done / total) * 100)) : 0;

  return (
    <div className="space-y-3" data-testid={testId}>
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="font-medium text-ink" data-testid={`${testId}-status`}>
          {t(`jobs.status.${job.status}`)}
        </span>
        {determinate && (
          <span className="font-mono tabular-nums text-muted" data-testid={`${testId}-progress`}>
            {t("jobs.progressOf", { done: job.progress_done, total })}
          </span>
        )}
      </div>
      {active && (
        <div
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={determinate ? total : undefined}
          aria-valuenow={determinate ? job.progress_done : undefined}
          className="h-1.5 w-full overflow-hidden rounded-full bg-surface-subtle"
          data-testid={`${testId}-bar`}
        >
          <div
            className={
              determinate
                ? "h-full bg-accent transition-[width] duration-300"
                : "h-full w-1/3 bg-accent motion-safe:animate-pulse"
            }
            style={determinate ? { width: `${percent}%` } : undefined}
          />
        </div>
      )}
      {job.progress_message && <p className="text-xs text-muted">{job.progress_message}</p>}
      {job.cancel_requested && active && (
        <p className="text-xs text-muted">{t("jobs.cancelRequested")}</p>
      )}
      {job.error && (
        <p className="text-xs text-danger" data-testid={`${testId}-error`}>
          {job.error}
        </p>
      )}
      {job.files.length > 0 && (
        <ul className="space-y-1">
          {job.files.map((f) => (
            <li key={f.id}>
              <a
                href={jobsApi.fileUrl(job.id, f.id)}
                download
                className="text-sm text-accent hover:underline"
                data-testid={`${testId}-file-${f.id}`}
              >
                {t("jobs.download", { name: f.name })}
              </a>
            </li>
          ))}
        </ul>
      )}
      {active && onCancel && (
        <Button
          variant="secondary"
          size="sm"
          onClick={onCancel}
          disabled={cancelling || job.cancel_requested}
          data-testid={`${testId}-cancel`}
        >
          {t("jobs.cancel")}
        </Button>
      )}
    </div>
  );
}
