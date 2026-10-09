import { useQuery } from "@tanstack/react-query";
import { useJob } from "@/components/jobs/useJob";
import { reportsApi, type ReportResult, type ReportRunIn } from "@/lib/reportsApi";

/**
 * Runs a report. `POST /reports/run` answers with the result (inline or from
 * the cache) or with a background job; for a job the hook polls it and hands
 * back the job's result once it succeeded. A failed or cancelled job becomes
 * `error` (Error with the job's text); request errors stay `ApiError` so the
 * caller can show the server's German message via `reportErrorMessage`.
 */
export function useReportRun(body: ReportRunIn | null, opts: { enabled?: boolean } = {}) {
  const enabled = body != null && opts.enabled !== false;
  const run = useQuery({
    queryKey: ["reports", "run", body],
    queryFn: ({ signal }) => reportsApi.run(body as ReportRunIn, signal),
    enabled,
    // A background job is started once per request; a refetch is explicit.
    staleTime: Infinity,
    retry: false,
  });

  const jobId = run.data?.job?.job_id ?? null;
  const { job, error: jobLoadError } = useJob(jobId);

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
