import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import type { Job } from "@/lib/jobsApi";
import { JobProgress } from "./JobProgress";
import { useJob } from "./useJob";

/** Modal that follows one background job: progress, cancel while it runs,
 * download links when it finished. Renders nothing without a `jobId`. */
export function JobDialog({
  jobId,
  title,
  onClose,
  onSucceeded,
  testId = "job-dialog",
}: {
  jobId: number | null;
  title: string;
  onClose: () => void;
  onSucceeded?: (job: Job) => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  const { job, isTerminal, error, cancel, isCancelling, cancelError } = useJob(jobId);

  const onSucceededRef = useRef(onSucceeded);
  onSucceededRef.current = onSucceeded;
  const notifiedFor = useRef<number | null>(null);
  useEffect(() => {
    if (job?.status === "succeeded" && notifiedFor.current !== job.id) {
      notifiedFor.current = job.id;
      onSucceededRef.current?.(job);
    }
  }, [job]);

  if (jobId == null) return null;

  return (
    <Dialog
      open
      onClose={onClose}
      title={title}
      size="sm"
      footer={
        job && !isTerminal ? (
          <Button
            variant="secondary"
            size="sm"
            onClick={cancel}
            disabled={isCancelling || job.cancel_requested}
            data-testid={`${testId}-cancel`}
          >
            {t("jobs.cancel")}
          </Button>
        ) : (
          <Button variant="secondary" size="sm" onClick={onClose} data-testid={`${testId}-close`}>
            {t("jobs.close")}
          </Button>
        )
      }
    >
      {job ? (
        <>
          <JobProgress job={job} testId={testId} />
          {cancelError && !isTerminal && (
            <p className="mt-3 text-xs text-danger" role="alert" data-testid={`${testId}-cancel-error`}>
              {apiErrorMessage(cancelError, t("jobs.cancelFailed"))}
            </p>
          )}
        </>
      ) : error ? (
        <p className="text-sm text-danger" data-testid={`${testId}-error`}>
          {t("jobs.loadError")}
        </p>
      ) : (
        <Spinner />
      )}
    </Dialog>
  );
}
