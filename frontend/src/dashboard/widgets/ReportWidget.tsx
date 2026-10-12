import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { WidgetMode, WidgetProps } from "@/plugins/dashboardWidgets";
import { ReportView } from "@/components/reports/ReportView";
import { useJob } from "@/components/jobs/useJob";
import { ApiError } from "@/lib/api";
import { dashboardsApi, settingsHash } from "@/lib/dashboardsApi";
import { reportErrorMessage, reportsApi, type ReportDefinition, type ReportResult } from "@/lib/reportsApi";
import { useWidgetData } from "../data";
import { useDashboardFilters } from "../filters/filtersContext";
import { WidgetFrame, useWidgetChrome } from "../chrome/WidgetFrame";
import { WidgetDataBody, WidgetEmpty } from "../chrome/WidgetDataBody";
import { WidgetError } from "../chrome/WidgetError";
import { WidgetSkeleton } from "../chrome/WidgetSkeleton";
import { skeletonRows } from "../chrome/mode";
import { ChooseSourceAction } from "./SourceAction";

export interface ReportWidgetSettings {
  report_key?: string;
  time_range?: string;
  parameters: Record<string, unknown>;
  dimension_choices: Record<string, string>;
  display: "auto" | "table";
  max_rows: number;
}

export interface ReportTileData {
  report?: { key: string | null; kind?: string; name?: string | null; title_key?: string | null };
  display?: "auto" | "table";
  deferred?: boolean;
  reason?: string;
  /** The server renders a wallboard: no job is ever started. */
  kiosk?: boolean;
  definition?: ReportDefinition;
  result?: ReportResult;
  ignored_filters?: string[];
  drillable_dimensions?: string[];
}

/** A deferred answer after the last job finished longer ago than this may start a new job (the cache entry expired). */
export const JOB_RESTART_AFTER_MS = 60_000;

type Run = { key: string; jobId: number | null; error: string | null; rateLimited: boolean };

/**
 * The background run of a deferred report. One job per `settings + filters` (the guard key); a
 * deferred answer that arrives more than 60 s after the last job finished may start another one.
 * When the job succeeded the widget is fetched again; until that answer is in (or with
 * `cache_seconds = 0`, where the result is never cached) the job's own result is shown.
 */
function useBackgroundRun({
  active,
  dashboardId,
  widgetId,
  guardKey,
  tick,
  refetch,
}: {
  active: boolean;
  dashboardId: number | undefined;
  widgetId: string;
  guardKey: string;
  /** Changes with every deferred answer. */
  tick: unknown;
  refetch: () => void;
}) {
  const { t } = useTranslation();
  const [run, setRun] = useState<Run | null>(null);
  const startedKey = useRef<string | null>(null);
  const finishedAt = useRef<number | null>(null);
  const handledJob = useRef<number | null>(null);
  const { job } = useJob(run?.jobId);

  const start = useCallback(() => {
    if (dashboardId === undefined) return;
    startedKey.current = guardKey;
    finishedAt.current = null;
    const mine: Run = { key: guardKey, jobId: null, error: null, rateLimited: false };
    setRun(mine);
    dashboardsApi.startReportJob(dashboardId, widgetId).then(
      (accepted) => setRun((r) => (r === mine ? { ...mine, jobId: accepted.job_id } : r)),
      (err: unknown) =>
        setRun((r) =>
          r === mine
            ? {
                ...mine,
                rateLimited: err instanceof ApiError && err.status === 429,
                error:
                  err instanceof ApiError && err.status === 429
                    ? t("dashboards.widgets.report.tooManyJobs")
                    : reportErrorMessage(err, t("dashboards.widgets.report.jobFailed")),
              }
            : r,
        ),
    );
  }, [dashboardId, widgetId, guardKey, t]);

  useEffect(() => {
    if (!active || dashboardId === undefined) return;
    const running = run !== null && run.key === guardKey && run.error === null && finishedAt.current === null;
    if (running) return;
    const sameKey = startedKey.current === guardKey;
    if (!sameKey) {
      start();
      return;
    }
    // Same settings: only a deferred answer long after the last job (expired cache) starts another.
    if (finishedAt.current !== null && Date.now() - finishedAt.current > JOB_RESTART_AFTER_MS) start();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `tick` is the trigger
  }, [active, dashboardId, guardKey, tick]);

  useEffect(() => {
    if (!job || run?.jobId == null || handledJob.current === run.jobId) return;
    if (job.status === "succeeded") {
      handledJob.current = run.jobId;
      finishedAt.current = Date.now();
      refetch();
    } else if (job.status === "failed" || job.status === "cancelled") {
      handledJob.current = run.jobId;
      finishedAt.current = Date.now();
      setRun((r) => (r && r.jobId === run.jobId ? { ...r, error: job.error || t("dashboards.widgets.report.jobFailed") } : r));
    }
  }, [job, run?.jobId, refetch, t]);

  const succeeded = job?.status === "succeeded" && run?.jobId === job.id;
  return {
    run: run && run.key === guardKey ? run : null,
    jobResult: succeeded ? ((job.result ?? undefined) as ReportResult | undefined) : undefined,
    retry: start,
  };
}

function Waiting({ children }: { children: string }) {
  return (
    <div role="status" className="flex h-full flex-col items-start justify-center gap-2 text-sm text-muted" data-testid="report-waiting">
      <p>{children}</p>
    </div>
  );
}

function RetryButton({ onClick, mode }: { onClick: () => void; mode: WidgetMode }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      tabIndex={mode === "view" ? undefined : -1}
      className="rounded-md border border-hairline bg-surface px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
    >
      {t("dashboards.state.retry")}
    </button>
  );
}

/** Table display: the report's own definition with the visualisation swapped. */
function shownDefinition(definition: ReportDefinition, display: "auto" | "table"): ReportDefinition {
  if (display !== "table") return definition;
  return { ...definition, visualization: { ...(definition.visualization ?? {}), type: "table" } } as ReportDefinition;
}

function ReportBody({
  data,
  settings,
  mode,
  size,
  refetch,
  bg,
  jobDefinition,
  reportKey,
  dashboardId,
}: {
  data: ReportTileData | undefined;
  settings: ReportWidgetSettings;
  mode: WidgetMode;
  size: { h: number };
  refetch: () => void;
  bg: ReturnType<typeof useBackgroundRun>;
  jobDefinition: ReportDefinition | undefined;
  reportKey: string | null;
  dashboardId: number | undefined;
}) {
  const { t } = useTranslation();
  if (!data) {
    return (
      <WidgetEmpty action={<ChooseSourceAction mode={mode} />}>{t("dashboards.widgets.report.empty")}</WidgetEmpty>
    );
  }

  if (data.deferred === true) {
    if (mode === "wallboard" || data.kiosk === true) return <Waiting>{t("dashboards.widgets.report.tooLargeWallboard")}</Waiting>;
    if (mode !== "view" || dashboardId === undefined) {
      return <Waiting>{t("dashboards.widgets.report.tooLargePreview")}</Waiting>;
    }
    if (bg.jobResult && jobDefinition) {
      return (
        <ReportView
          report={{ key: reportKey, definition: shownDefinition(jobDefinition, settings.display) }}
          result={{ ...bg.jobResult, rows: bg.jobResult.rows.slice(0, settings.max_rows) }}
          compact
          testId="report-widget-view"
        />
      );
    }
    if (bg.run?.error) {
      return (
        <div role="alert" className="flex h-full flex-col items-start justify-center gap-2.5 text-sm" data-testid="report-job-error">
          <p className="text-danger">{bg.run.error}</p>
          <RetryButton onClick={bg.retry} mode={mode} />
        </div>
      );
    }
    return (
      <div className="flex h-full flex-col gap-2" data-testid="report-job-running">
        <Waiting>{t("dashboards.widgets.report.running")}</Waiting>
        <WidgetSkeleton rows={Math.max(1, skeletonRows(size.h) - 2)} />
      </div>
    );
  }

  if (!data.definition || !data.result) {
    return <WidgetError message={t("dashboards.state.loadFailed")} onRetry={refetch} />;
  }
  const ignored = data.ignored_filters ?? [];
  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="min-h-0 flex-1 overflow-auto">
        <ReportView
          report={{ key: data.report?.key ?? null, definition: shownDefinition(data.definition, settings.display) }}
          result={data.result}
          compact
          testId="report-widget-view"
        />
      </div>
      {ignored.includes("queue") && (
        <p className="text-xs text-muted" data-testid="report-ignored-filters">
          {t("dashboards.widgets.report.queueIgnored")}
        </p>
      )}
    </div>
  );
}

/** A report on the dashboard: the report's own visualisation (compact) or a table; large reports run as a background job. */
export function ReportWidget({ instanceId, settings, size, mode }: WidgetProps<ReportWidgetSettings>) {
  const { t } = useTranslation();
  const { status, data, refetch } = useWidgetData<ReportTileData>();
  const chrome = useWidgetChrome();
  const { filters } = useDashboardFilters();
  const tile = status === "ok" ? data : undefined;
  const deferred = tile?.deferred === true;
  // The job state lives here, above the data gate: refetching after the job swaps the body for a skeleton.
  const bg = useBackgroundRun({
    active: deferred && mode === "view" && tile?.kiosk !== true && chrome.dashboardId !== undefined,
    dashboardId: chrome.dashboardId,
    widgetId: instanceId,
    guardKey: settingsHash({ settings, filters }),
    tick: tile,
    refetch,
  });
  const reportKey = tile?.report?.key ?? settings.report_key ?? null;
  // The job's own result needs the report's definition (a deferred answer carries none).
  const reportQ = useQuery({
    queryKey: ["reports", "get", reportKey],
    queryFn: ({ signal }) => reportsApi.get(reportKey as string, signal),
    enabled: deferred && bg.jobResult !== undefined && reportKey !== null,
    retry: false,
  });
  const title =
    (tile?.report?.name ?? "") || (tile?.report?.title_key ? t(tile.report.title_key) : "") || t("dashboards.widgets.report.title");
  return (
    <WidgetFrame title={title} mode={mode} testId="report-widget">
      <WidgetDataBody<ReportTileData> h={size.h}>
        {(d) => (
          <ReportBody
            data={status === "ok" ? d : undefined}
            settings={settings}
            mode={mode}
            size={size}
            refetch={refetch}
            bg={bg}
            jobDefinition={reportQ.data?.definition ?? undefined}
            reportKey={reportKey}
            dashboardId={chrome.dashboardId}
          />
        )}
      </WidgetDataBody>
    </WidgetFrame>
  );
}
