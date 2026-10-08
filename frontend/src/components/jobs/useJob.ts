import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { TERMINAL_JOB_STATUSES, jobsApi, type Job } from "@/lib/jobsApi";

const FAST_POLL_MS = 1000;
const SLOW_POLL_MS = 3000;
const FAST_POLL_WINDOW_MS = 30_000;

/** Poll interval for a job: `false` once it is finished, 1 s during the
 * first 30 s after it was first seen, 3 s after that. */
export function jobPollInterval(
  job: Job | undefined,
  firstSeenMs: number,
  nowMs: number,
  failedWithoutData = false,
): number | false {
  if (job && TERMINAL_JOB_STATUSES.has(job.status)) return false;
  // A job that cannot be loaded at all (404 after a purge, 403) will not appear later.
  if (!job && failedWithoutData) return false;
  return nowMs - firstSeenMs < FAST_POLL_WINDOW_MS ? FAST_POLL_MS : SLOW_POLL_MS;
}

export function useJob(jobId: number | null | undefined) {
  const qc = useQueryClient();
  const firstSeen = useRef<{ id: number | null | undefined; at: number }>({ id: undefined, at: 0 });
  if (firstSeen.current.id !== jobId) firstSeen.current = { id: jobId, at: Date.now() };

  const query = useQuery({
    queryKey: ["jobs", jobId],
    queryFn: ({ signal }) => jobsApi.get(jobId as number, signal),
    enabled: jobId != null,
    refetchInterval: (q) =>
      jobPollInterval(
        q.state.data,
        firstSeen.current.at,
        Date.now(),
        q.state.status === "error" && q.state.data === undefined,
      ),
  });

  const cancelMutation = useMutation({
    mutationFn: () => jobsApi.cancel(jobId as number),
    onSuccess: (job) => qc.setQueryData(["jobs", jobId], job),
  });

  const job = query.data;
  return {
    job,
    isTerminal: job ? TERMINAL_JOB_STATUSES.has(job.status) : false,
    error: query.error,
    cancel: () => {
      if (jobId == null) return;
      // `mutate`, not `mutateAsync`: a failure lands in `cancelError` instead of
      // an unhandled rejection.
      cancelMutation.mutate();
    },
    isCancelling: cancelMutation.isPending,
    cancelError: cancelMutation.error,
  };
}
