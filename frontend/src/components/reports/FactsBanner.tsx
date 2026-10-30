import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { reportsApi, type FactsStatus } from "@/lib/reportsApi";

/** How often the build progress is re-read while the data is being built. */
const FACTS_POLL_MS = 15_000;
/** The fact upgrade after an update takes hours: the count behind its notice is re-read rarely. */
const UPGRADE_POLL_MS = 5 * 60_000;
/**
 * One batch of the worker (`VERSION_UPGRADE_BATCH`). A smaller remainder is gone on its next run or is
 * tickets that keep failing, which no reader can act on: the exact count stays with the admin and the CLI.
 */
const UPGRADE_NOTICE_MIN_ROWS = 500;

/**
 * Notice while the report data is still being built (first start, rebuild):
 * the numbers are incomplete until then. Admins follow the rebuild's progress,
 * everyone else sees the percentage. A second notice while the fact upgrade after
 * an update recomputes old rows.
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
  // A report result carries no upgrade count, so the status is read as well, at most once per slow poll.
  const statusQ = useQuery({
    queryKey: ["reports", "facts"],
    queryFn: ({ signal }) => reportsApi.factsStatus(signal),
    enabled: facts != null,
    staleTime: UPGRADE_POLL_MS,
    refetchInterval: (query) =>
      facts != null && !facts.ready
        ? FACTS_POLL_MS
        : (query.state.data?.outdated_rows ?? 0) > UPGRADE_NOTICE_MIN_ROWS
          ? UPGRADE_POLL_MS
          : false,
  });
  const current = facts != null && !facts.ready ? (statusQ.data ?? facts) : facts;
  const building = current != null && !current.ready;
  // Not tied to `facts`: a report re-run has none for a moment, and that does not end the upgrade.
  const outdated = statusQ.data?.outdated_rows ?? facts?.outdated_rows ?? 0;
  const upgrading = !building && outdated > UPGRADE_NOTICE_MIN_ROWS;
  const finished = facts != null && !facts.ready && statusQ.data?.ready === true;
  const wasUpgrading = useRef(false);
  useEffect(() => {
    // Built or upgraded meanwhile: reload the catalogue and the reports with complete numbers.
    const upgraded = wasUpgrading.current && !upgrading;
    wasUpgrading.current = upgrading;
    if (finished || upgraded) void queryClient.invalidateQueries({ queryKey: ["reports"] });
  }, [finished, upgrading, queryClient]);
  const jobId = building && user?.is_admin ? (current.backfill_job_id ?? null) : null;
  const { job } = useJob(jobId);
  if (!building && !upgrading) return null;

  const progress = current?.backfill_progress;
  const text = !building
    ? t("reports.factsUpgrading")
    : progress != null && progress > 0 && progress < 1
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
