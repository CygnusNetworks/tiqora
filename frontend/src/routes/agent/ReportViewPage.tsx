import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { downloadUrl } from "@/components/customers/vcardExport";
import { flattenQueues } from "@/components/agent/QueueTree";
import { FactsBanner } from "@/components/reports/FactsBanner";
import { ReportExportButton } from "@/components/reports/ReportExportButton";
import { ReportFilterBar } from "@/components/reports/ReportFilterBar";
import {
  filterRange,
  hasQueueParameter,
  missingRequired,
  rangeInvalid,
  runtimeParameters,
  toRunIn,
  type ReportFilterState,
} from "@/components/reports/reportFilter";
import { usePluginReportTexts } from "@/components/reports/usePluginReportTexts";
import { ReportParameterControls } from "@/components/reports/ReportParameterControls";
import { ReportsAccessGate } from "@/components/reports/ReportsAccessGate";
import { ReportView } from "@/components/reports/ReportView";
import { useReportRun } from "@/components/reports/useReportRun";
import { Button } from "@/components/ui/Button";
import { Menu, MenuItem } from "@/components/ui/Menu";
import { Spinner } from "@/components/ui/Spinner";
import { toBcp47 } from "@/i18n/locales";
import { api, ApiError } from "@/lib/api";
import { formatCalendarDay, formatDateTime } from "@/lib/format";
import {
  isSavedKey,
  reportDescription,
  reportErrorMessage,
  reportTitle,
  reportsApi,
} from "@/lib/reportsApi";
import { filterFromSearch, searchFromFilter, type ReportViewSearch } from "./reportViewSearch";

const actionCls =
  "inline-flex items-center rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** `/agent/reports/$reportKey`: one report with its filters, chart/table and export. */
function ReportViewPageContent() {
  const { t, i18n } = useTranslation();
  const bcp = toBcp47(i18n.resolvedLanguage ?? i18n.language ?? "de");
  const { reportKey } = useParams({ from: "/agent/reports/$reportKey" });
  const search = useSearch({ from: "/agent/reports/$reportKey" }) as ReportViewSearch;
  const navigate = useNavigate();
  const { user } = useAuth();
  const canCreate = user?.can_create_reports === true;

  const reportQ = useQuery({
    queryKey: ["reports", "get", reportKey],
    queryFn: ({ signal }) => reportsApi.get(reportKey, signal),
    retry: false,
  });
  const report = reportQ.data;
  usePluginReportTexts(useMemo(() => [report], [report]));
  const definition = report?.definition ?? undefined;
  const state = useMemo(() => filterFromSearch(search, definition), [search, definition]);
  const body = useMemo(
    // A start after the end is never sent; the filter bar says what to fix.
    // A required parameter without a value waits for one (the controls say which).
    () =>
      definition && !rangeInvalid(state) && missingRequired(definition, state.params).length === 0
        ? toRunIn(reportKey, state, definition)
        : null,
    [reportKey, state, definition],
  );
  const run = useReportRun(body);
  const result = run.result;

  const queuesQ = useQuery({
    queryKey: ["queues"],
    queryFn: () => api.listQueues(),
    enabled: definition ? hasQueueParameter(definition) : false,
  });

  const hasParams = definition ? runtimeParameters(definition).length > 0 : false;
  const sourcesQ = useQuery({
    queryKey: ["reports", "sources"],
    queryFn: ({ signal }) => reportsApi.sources(signal),
    enabled: hasParams,
  });
  const source = sourcesQ.data?.find((s) => s.key === definition?.source);

  const onChange = (next: ReportFilterState) => {
    void navigate({
      to: "/agent/reports/$reportKey",
      params: { reportKey },
      search: searchFromFilter(next),
      replace: true,
    });
  };

  const title = report ? reportTitle(report, t) : "";
  const description = report ? reportDescription(report, t) : "";
  const saved = report?.kind === "saved" && isSavedKey(reportKey);
  const gone = reportQ.error instanceof ApiError && reportQ.error.status === 410;
  const notFound = reportQ.error instanceof ApiError && reportQ.error.status === 404;
  const range = result?.time_window
    ? { from: result.time_window.start_date, to: result.time_window.end_date }
    : filterRange(state);
  const rangeText = range ? `${formatCalendarDay(range.from, bcp)} – ${formatCalendarDay(range.to, bcp)}` : null;
  const zone = result?.time_window?.time_zone ?? body?.time_zone ?? null;
  const queueNames = state.queues.map(
    (id) => flattenQueues(queuesQ.data ?? []).find((q) => q.id === id)?.name ?? `#${id}`,
  );
  const infoLines = [
    rangeText ? t("reports.info.timeRange", { range: rangeText }) : null,
    zone ? t("reports.info.timeZone", { zone }) : null,
    queueNames.length > 0 ? t("reports.info.queues", { queues: queueNames.join(", ") }) : null,
  ].filter((l): l is string => l !== null);

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5 px-4 py-6" data-testid="report-view-page">
      <Link
        to="/agent/reports"
        className="inline-flex items-center gap-1 text-xs text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        data-testid="report-view-back"
      >
        <span aria-hidden>←</span> {t("reports.back")}
      </Link>

      {reportQ.isPending ? (
        <Spinner />
      ) : gone || notFound ? (
        <div
          className="space-y-3 rounded-lg border border-hairline bg-surface px-4 py-6 text-sm text-ink"
          role="status"
          data-testid={gone ? "report-view-gone" : "report-view-notfound"}
        >
          <p>{gone ? t("reports.saved.deleted") : t("reports.saved.notFound")}</p>
          <Link
            to="/agent/reports"
            className="inline-flex text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            data-testid="report-view-gone-back"
          >
            {t("reports.saved.backToList")}
          </Link>
        </div>
      ) : !report || !definition ? (
        <p className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-ink" role="alert" data-testid="report-error">
          {report?.definition_error ?? reportErrorMessage(reportQ.error, t("reports.error"))}
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 max-w-3xl">
              <h1 className="font-display text-2xl font-semibold tracking-tight text-ink">{title}</h1>
              <p className="mt-1 text-sm text-muted">{description}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {saved && !report.read_only && canCreate && (
                <Link
                  to="/agent/reports/$reportKey/edit"
                  params={{ reportKey }}
                  className={actionCls}
                  data-testid="report-view-edit"
                >
                  {t("reports.saved.edit")}
                </Link>
              )}
              {saved && canCreate && (
                <Link to="/agent/reports/new" search={{ from: reportKey }} className={actionCls} data-testid="report-view-copy">
                  {t("reports.saved.copy")}
                </Link>
              )}
              {saved && (
                <Menu
                  panelTestId="report-view-download-menu"
                  trigger={({ ref, toggleProps }) => (
                    <button type="button" ref={ref} {...toggleProps} className={actionCls} data-testid="report-view-download">
                      {t("reports.saved.download")}
                    </button>
                  )}
                >
                  <MenuItem testId="report-view-download-json" onSelect={() => downloadUrl(reportsApi.fileUrl(reportKey, "json"))}>
                    {t("reports.saved.exportJson")}
                  </MenuItem>
                  <MenuItem testId="report-view-download-yaml" onSelect={() => downloadUrl(reportsApi.fileUrl(reportKey, "yaml"))}>
                    {t("reports.saved.exportYaml")}
                  </MenuItem>
                </Menu>
              )}
              {canCreate && (
                <Link
                  to="/agent/reports/schedules/new"
                  search={{ report: reportKey }}
                  className={actionCls}
                  data-testid="report-view-schedule"
                >
                  {t("reports.schedules.create")}
                </Link>
              )}
              {body && <ReportExportButton runIn={body} result={result} title={title} infoLines={infoLines} />}
            </div>
          </div>

          <FactsBanner facts={result?.facts} />

          <ReportFilterBar
            definition={definition}
            value={state}
            onChange={onChange}
            defaultRange={
              result?.time_window ? { from: result.time_window.start_date, to: result.time_window.end_date } : null
            }
          />

          {hasParams && !sourcesQ.isPending && (
            <ReportParameterControls
              definition={definition}
              source={source}
              values={state.params ?? {}}
              onChange={(params) => onChange({ ...state, params })}
              testId="report-params"
            />
          )}

          {body && (
          <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4" aria-live="polite">
            {(rangeText || result) && (
              <p className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted" data-testid="report-view-meta">
                {rangeText && (
                  <span>
                    {t("reports.timeRange")}: <span className="text-ink">{rangeText}</span>
                  </span>
                )}
                {zone && (
                  <span>
                    {t("reports.timeZone")}: <span className="text-ink">{zone}</span>
                  </span>
                )}
                {result && (
                  <span>
                    {t("reports.factsAsOf", { time: formatDateTime(result.generated_at, bcp) })}
                    {result.cached && <> · {t("reports.cached")}</>}
                  </span>
                )}
              </p>
            )}
            {run.error ? (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-danger/40 bg-danger/10 px-4 py-3">
                <p className="text-sm text-ink" role="alert" data-testid="report-error">
                  {reportErrorMessage(run.error, t("reports.error"))}
                </p>
                <Button size="sm" variant="secondary" onClick={run.refetch} data-testid="report-retry">
                  {t("reports.retry")}
                </Button>
              </div>
            ) : result ? (
              <ReportView key={reportKey} report={{ key: reportKey, definition }} result={result} />
            ) : (
              <div className="flex items-center gap-3 py-10 text-sm text-muted" data-testid="report-loading">
                <Spinner />
                <span>{run.jobId != null ? t("reports.runningInBackground") : t("reports.loading")}</span>
              </div>
            )}
          </section>
          )}
        </>
      )}
    </div>
  );
}

export function ReportViewPage() {
  return (
    <ReportsAccessGate>
      <ReportViewPageContent />
    </ReportsAccessGate>
  );
}
