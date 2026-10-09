import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Spinner } from "@/components/ui/Spinner";
import { JobDialog } from "@/components/jobs/JobDialog";
import { StatTile } from "@/components/agent/stats/StatTile";
import { reportErrorMessage, reportsApi } from "@/lib/reportsApi";
import { jobsApi } from "@/lib/jobsApi";
import { formatCalendarDay, formatDateTime } from "@/lib/format";

const QUERY_KEY = ["admin", "reporting", "status"] as const;
const REFETCH_INTERVAL_MS = 10_000;

/** Admin page "Berichtsdaten": state of the report data plus a full rebuild. */
export function ReportingAdminPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const qc = useQueryClient();
  const [since, setSince] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [jobId, setJobId] = useState<number | null>(null);

  const statusQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => reportsApi.adminStatus(signal),
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  // The status only knows pending/running/done; whether a pending build is
  // queued or failed lives on its job.
  const buildJobId = statusQ.data && statusQ.data.backfill_status !== "done" ? statusQ.data.backfill_job_id : null;
  const buildJobQ = useQuery({
    queryKey: ["admin", "reporting", "build-job", buildJobId],
    queryFn: ({ signal }) => jobsApi.get(buildJobId as number, signal),
    enabled: buildJobId != null,
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const rebuild = useMutation({
    mutationFn: () =>
      reportsApi.adminBackfill({ since: since || null, restart: true, snapshots: true }),
    onSuccess: (accepted) => {
      setConfirming(false);
      setJobId(accepted.job_id);
      void qc.invalidateQueries({ queryKey: QUERY_KEY });
    },
  });

  const s = statusQ.data;
  const num = (n: number) => new Intl.NumberFormat(locale).format(n);
  const jobStatus = buildJobQ.data?.status;
  const buildFailed = s?.backfill_status === "pending" && (jobStatus === "failed" || jobStatus === "cancelled");
  const buildQueued = s?.backfill_status === "pending" && !buildFailed && buildJobId != null;
  const buildActive = s?.backfill_status === "running" || buildQueued;
  const stateLabel = !s
    ? ""
    : s.backfill_status === "running"
      ? s.backfill_progress != null
        ? t("admin.reporting.buildingPercent", { percent: Math.round(s.backfill_progress * 100) })
        : t("admin.reporting.building")
      : buildFailed
        ? t("admin.reporting.failed")
        : buildQueued
          ? t("admin.reporting.queued")
          : s.ready
            ? t("admin.reporting.ready")
            : t("admin.reporting.pending");

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="admin-reporting-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">
            {t("admin.reporting.title")}
          </h1>
          <p className="mt-1 text-sm text-muted">{t("admin.reporting.subtitle")}</p>
        </div>
        <Link
          to="/admin/settings/$section"
          params={{ section: "reporting" }}
          className="text-sm text-accent hover:underline"
          data-testid="admin-reporting-settings"
        >
          {t("admin.reporting.settings")}
        </Link>
      </div>

      {statusQ.isLoading && <Spinner />}
      {statusQ.isError && (
        <p className="text-sm text-danger" role="alert" data-testid="admin-reporting-error">
          {reportErrorMessage(statusQ.error, t("admin.reporting.loadError"))}
        </p>
      )}

      {s && (
        <section className="space-y-3 rounded-xl border border-hairline bg-surface p-4">
          <p className="text-sm font-semibold text-ink" data-testid="admin-reporting-state">
            {stateLabel}
          </p>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            <StatTile
              testId="admin-reporting-as-of"
              label={t("admin.reporting.asOf")}
              value={s.as_of ? formatDateTime(s.as_of, locale) : "—"}
            />
            <StatTile
              testId="admin-reporting-lag"
              label={t("admin.reporting.lag")}
              value={num(s.history_lag)}
              hint={t("admin.reporting.lagHint")}
              tone={s.history_lag > 1000 ? "warn" : "default"}
            />
            <StatTile
              testId="admin-reporting-gaps"
              label={t("admin.reporting.gaps")}
              value={num(s.gap_count)}
              hint={t("admin.reporting.gapsHint")}
            />
            <StatTile
              testId="admin-reporting-dirty"
              label={t("admin.reporting.dirty")}
              value={num(s.dirty_count)}
              hint={t("admin.reporting.dirtyHint")}
            />
            <StatTile
              testId="admin-reporting-last-snapshot"
              label={t("admin.reporting.lastSnapshot")}
              value={s.last_snapshot_date ? formatCalendarDay(s.last_snapshot_date, locale) : "—"}
              hint={s.snapshot_time_zone ?? undefined}
            />
            <StatTile
              testId="admin-reporting-fact-rows"
              label={t("admin.reporting.factRows")}
              value={num(s.fact_rows)}
            />
            <StatTile
              testId="admin-reporting-event-rows"
              label={t("admin.reporting.eventRows")}
              value={num(s.event_rows)}
            />
          </div>
        </section>
      )}

      <section className="space-y-3 rounded-xl border border-hairline bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">{t("admin.reporting.rebuild")}</h2>
        <p className="text-sm text-muted">{t("admin.reporting.rebuildHelp")}</p>
        <label className="block text-sm text-ink">
          {t("admin.reporting.rebuildSince")}
          <input
            type="date"
            value={since}
            onChange={(e) => setSince(e.target.value)}
            data-testid="admin-reporting-since"
            className="mt-1 block rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
          />
        </label>
        <Button
          size="sm"
          onClick={() => setConfirming(true)}
          disabled={buildActive}
          data-testid="admin-reporting-rebuild"
        >
          {t("admin.reporting.rebuild")}
        </Button>
        {rebuild.isError && (
          <p className="text-sm text-danger" role="alert" data-testid="admin-reporting-rebuild-error">
            {reportErrorMessage(rebuild.error, t("admin.reporting.rebuildError"))}
          </p>
        )}
      </section>

      <ConfirmDialog
        open={confirming}
        title={t("admin.reporting.rebuild")}
        message={t("admin.reporting.rebuildConfirm")}
        confirmLabel={t("admin.reporting.rebuildStart")}
        pending={rebuild.isPending}
        onConfirm={() => rebuild.mutate()}
        onCancel={() => setConfirming(false)}
      />
      <JobDialog
        jobId={jobId}
        title={t("jobs.kinds.core.report_backfill")}
        testId="admin-reporting-job"
        onClose={() => {
          setJobId(null);
          void qc.invalidateQueries({ queryKey: QUERY_KEY });
        }}
        onSucceeded={() => void qc.invalidateQueries({ queryKey: QUERY_KEY })}
      />
    </div>
  );
}
