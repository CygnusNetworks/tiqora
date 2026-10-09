import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useJob } from "@/components/jobs/useJob";
import { TERMINAL_JOB_STATUSES, jobsApi } from "@/lib/jobsApi";
import { reportsApi, type ReportResult, type ReportRunIn } from "@/lib/reportsApi";

/**
 * Runs a report. `POST /reports/run` answers with the result (inline or from
 * the cache) or with a background job; for a job the hook polls it and hands
 * back the job's result once it succeeded. A failed or cancelled job becomes
 * `error` (Error with the job's text); request errors stay `ApiError` so the
 * caller can show the server's German message via `reportErrorMessage`.
 *
 * A still-active job is cancelled (best effort) when the request changes or the
 * component unmounts, so quick preset changes do not fill the per-user job cap.
 */
export function useReportRun(body: ReportRunIn | null, opts: { enabled?: boolean } = {}) {
  const queryClient = useQueryClient();
  const enabled = body != null && opts.enabled !== false;
  const run = useQuery({
    queryKey: ["reports", "run", body],
    queryFn: ({ signal }) => reportsApi.run(body as ReportRunIn, signal),
    enabled,
    // Live reports ("open now") must not stay frozen after a remount.
    staleTime: 60_000,
    retry: false,
  });

  const jobId = run.data?.job?.job_id ?? null;
  const { job, error: jobLoadError } = useJob(jobId);

  const statusById = useRef(new Map<number, string>());
  if (jobId != null && job?.status) statusById.current.set(jobId, job.status);
  const liveJobRef = useRef<number | null>(null);
  useEffect(() => {
    if (jobId == null) return;
    liveJobRef.current = jobId;
    const statuses = statusById.current;
    return () => {
      liveJobRef.current = null;
      // Deferred so StrictMode's simulated unmount/remount does not cancel a live job.
      setTimeout(() => {
        if (liveJobRef.current === jobId) return;
        const status = statuses.get(jobId);
        if (status && TERMINAL_JOB_STATUSES.has(status as never)) return;
        void Promise.resolve(jobsApi.cancel(jobId)).catch(() => {});
        // Forget the cancelled run: coming back to this request must rerun it, not show "cancelled".
        queryClient.removeQueries({ queryKey: ["reports", "run", body], exact: true });
      }, 0);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- body only names the cache entry of this job
  }, [jobId]);

  let result: ReportResult | undefined = run.data?.result ?? undefined;
  let error: unknown = run.error ?? null;
  let jobPending = false;
  if (jobId != null) {
    if (job?.status === "succeeded") {
      result = (job.result ?? undefined) as ReportResult | undefined;
      if (!result) error = new Error("Der Bericht hat kein Ergebnis geliefert.");
    } else if (job?.status === "failed") {
      error = new Error(job.error ?? "Der Bericht ist fehlgeschlagen.");
    } else if (job?.status === "cancelled") {
      error = new Error(job.error ?? "Der Bericht wurde abgebrochen.");
    } else if (jobLoadError && !job) {
      error = jobLoadError;
    } else {
      jobPending = true;
    }
  }

  return {
    result,
    jobId,
    isLoading: enabled && (run.isPending || run.isFetching || jobPending) && !error,
    error: error ?? null,
    refetch: () => {
      void run.refetch();
    },
  };
}
