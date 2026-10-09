import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { reportsApi, type FactsStatus } from "@/lib/reportsApi";

/** How often the build progress is re-read while the data is being built. */
const FACTS_POLL_MS = 15_000;

/**
 * Notice while the report data is still being built (first start, rebuild):
 * the numbers are incomplete until then. Admins follow the rebuild's progress,
 * everyone else sees the percentage.
 */
export function FactsBanner({
  facts,
  testId = "reports-facts-banner",
}: {
  facts: FactsStatus | null | undefined;
  testId?: string;
}) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  // The page's own status (catalogue/result) is read once; while building, re-read it so
  // everyone sees the percentage move and the banner goes away when the build is done.
  const statusQ = useQuery({
    queryKey: ["reports", "facts"],
    queryFn: ({ signal }) => reportsApi.factsStatus(signal),
    enabled: facts != null && !facts.ready,
    refetchInterval: FACTS_POLL_MS,
  });
  const current = facts != null && !facts.ready ? (statusQ.data ?? facts) : facts;
  const building = current != null && !current.ready;
  const finished = facts != null && !facts.ready && statusQ.data?.ready === true;
  useEffect(() => {
    // Built meanwhile: reload the catalogue and the reports with complete numbers.
    if (finished) void queryClient.invalidateQueries({ queryKey: ["reports"] });
  }, [finished, queryClient]);
  const jobId = building && user?.is_admin ? (current.backfill_job_id ?? null) : null;
  const { job } = useJob(jobId);
  if (!building) return null;

  const progress = current.backfill_progress;
  const text =
    progress != null && progress > 0 && progress < 1
      ? t("reports.factsBuildingProgress", { percent: Math.floor(progress * 100) })
      : t("reports.factsBuilding");

  return (
    <div
      role="status"
      className="flex flex-col gap-2 rounded-lg border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-ink"
      data-testid={testId}
    >
      <p className="flex items-start gap-2">
        <svg viewBox="0 0 16 16" className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden>
          <path d="M8 1.5 15 14H1z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
          <path d="M8 6v3.5M8 11.5v.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <span>{text}</span>
      </p>
      {job && (
        <div className="max-w-md">
          <JobProgress job={job} testId={`${testId}-job`} />
        </div>
      )}
    </div>
  );
}
