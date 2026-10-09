import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import type { FactsStatus } from "@/lib/reportsApi";

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
  const building = facts != null && !facts.ready;
  const jobId = building && user?.is_admin ? (facts.backfill_job_id ?? null) : null;
  const { job } = useJob(jobId);
  if (!building) return null;

  const progress = facts.backfill_progress;
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
