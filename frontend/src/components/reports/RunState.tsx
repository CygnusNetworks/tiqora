import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { reportErrorMessage } from "@/lib/reportsApi";
import type { useReportRun } from "./useReportRun";

/** Error with a retry button, or a spinner with a hint, for a report run that has no result yet. */
export function RunState({
  run,
  testId,
  minHeight = "min-h-[8rem]",
}: {
  run: Pick<ReturnType<typeof useReportRun>, "error" | "jobId" | "waitingForSlot" | "refetch">;
  testId: string;
  minHeight?: string;
}) {
  const { t } = useTranslation();
  if (run.error) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-danger/40 bg-danger/10 px-4 py-3">
        <p className="text-sm text-ink" role="alert" data-testid={`${testId}-error`}>
          {reportErrorMessage(run.error, t("reports.error"))}
        </p>
        <Button size="sm" variant="secondary" onClick={run.refetch} data-testid={`${testId}-retry`}>
          {t("reports.retry")}
        </Button>
      </div>
    );
  }
  return (
    <div className={`flex ${minHeight} flex-col items-center justify-center gap-2`} data-testid={`${testId}-loading`}>
      <Spinner />
      <span className="text-xs text-muted">
        {run.waitingForSlot
          ? t("reports.waitingForSlot")
          : run.jobId != null
            ? t("reports.runningInBackground")
            : t("reports.loading")}
      </span>
    </div>
  );
}
