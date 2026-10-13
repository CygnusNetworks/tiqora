import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Spinner } from "@/components/ui/Spinner";
import { JobDialog } from "@/components/jobs/JobDialog";
import { ImportRunReport } from "@/components/itsm-import/ImportRunReport";
import { ImportStatusCard } from "@/components/itsm-import/ImportStatusCard";
import { RollbackDialog } from "@/components/itsm-import/RollbackDialog";
import { SwitchDialog } from "@/components/itsm-import/SwitchDialog";
import { apiErrorMessage } from "@/lib/avatarApi";
import { formatDateTime } from "@/lib/format";
import { itsmImportApi, itsmImportKeys, type ImportRunOut } from "@/lib/itsmImportApi";

const REFETCH_INTERVAL_MS = 10_000;
const ROLLBACK_DOCS_URL = "https://github.com/CygnusNetworks/tiqora/blob/main/docs/itsm.md#zurück-zu-modus-a";

/** Admin page "ITSM-Daten aus Znuny übernehmen": dry run, import and the run reports. */
export function ItsmImportPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.resolvedLanguage ?? i18n.language);
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [jobId, setJobId] = useState<number | null>(null);
  const [openRun, setOpenRun] = useState<number | null>(null);
  const [switching, setSwitching] = useState(false);
  const [switchJobId, setSwitchJobId] = useState<number | null>(null);
  const [rollingBack, setRollingBack] = useState(false);

  const statusQ = useQuery({
    queryKey: itsmImportKeys.status(),
    queryFn: ({ signal }) => itsmImportApi.status(signal),
    // poll while a job is active so the buttons and the last run follow it
    refetchInterval: (q) => (q.state.data?.active_job_id != null ? REFETCH_INTERVAL_MS : false),
  });
  const runsQ = useQuery({
    queryKey: itsmImportKeys.runs(),
    queryFn: ({ signal }) => itsmImportApi.runs(signal),
    refetchInterval: statusQ.data?.active_job_id != null ? REFETCH_INTERVAL_MS : false,
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: itsmImportKeys.status() });
    void qc.invalidateQueries({ queryKey: itsmImportKeys.runs() });
  };

  const start = useMutation({
    mutationFn: (dryRun: boolean) => itsmImportApi.startRun(dryRun),
    onSuccess: (accepted) => {
      setConfirming(false);
      setJobId(accepted.job_id);
      refresh();
    },
    onError: () => setConfirming(false),
  });
  const releaseLock = useMutation({
    mutationFn: () => itsmImportApi.releaseLock(),
    onSuccess: refresh,
  });

  const s = statusQ.data;
  const canImport = !!s?.can_import;
  const reason = s && !s.can_import ? (s.blocked_reason ?? t("admin.itsmImport.blockedFallback")) : undefined;
  const busy = start.isPending;
  const inModeA = s?.mode === "znuny_readthrough";
  const switchReason = s && !s.can_switch ? (s.blocked_reason ?? t("admin.itsmImport.switch.blockedFallback")) : undefined;
  // Znuny ITSM is there but its release is not readable: only "Umstellen ohne CI-Daten" is left.
  const withoutCiData = !!s && s.znuny_itsm_present && s.profile == null;
  const switchedBy =
    s?.switched?.run_id != null ? runsQ.data?.find((r) => r.id === s.switched?.run_id)?.started_by_name : null;
  const drift = s?.znuny_changes_since_switch ?? 0;

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="admin-itsm-import-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.itsmImport.title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("admin.itsmImport.intro")}</p>
      </div>

      {statusQ.isLoading && <Spinner />}
      {statusQ.isError && (
        <p className="text-sm text-danger" role="alert" data-testid="itsm-import-error">
          {apiErrorMessage(statusQ.error, t("admin.itsmImport.loadError"))}
        </p>
      )}

      {s && (
        <>
          <ImportStatusCard status={s} />

          {s.mode === "tiqora" && (
            <div className="space-y-2">
              <p
                className="rounded-lg border border-green bg-green/10 p-3 text-sm text-ink"
                data-testid="itsm-switched-banner"
              >
                {s.switched?.switched_at
                  ? switchedBy
                    ? t("admin.itsmImport.switched.bannerBy", {
                        date: formatDateTime(s.switched.switched_at, locale),
                        name: switchedBy,
                      })
                    : t("admin.itsmImport.switched.banner", { date: formatDateTime(s.switched.switched_at, locale) })
                  : t("admin.itsmImport.switched.bannerNoDate")}
              </p>
              {drift > 0 && (
                <p
                  className="rounded-lg border border-warn bg-warn/10 p-3 text-sm text-ink"
                  role="alert"
                  data-testid="itsm-drift-warning"
                >
                  {t("admin.itsmImport.switched.drift", { count: drift })}{" "}
                  <a
                    href={ROLLBACK_DOCS_URL}
                    target="_blank"
                    rel="noreferrer"
                    className="underline"
                    data-testid="itsm-drift-docs"
                  >
                    {t("admin.itsmImport.switched.driftDocs")}
                  </a>
                </p>
              )}
              {/* only a switch can be rolled back; a site that started in Mode B has none */}
              {s.switched != null && (
                <Button variant="ghost" size="sm" onClick={() => setRollingBack(true)} data-testid="itsm-rollback-open">
                  {t("admin.itsmImport.rollback.open")}
                </Button>
              )}
            </div>
          )}

          {reason && (
            <p className="text-sm text-warn" data-testid="itsm-import-blocked">
              {reason}
            </p>
          )}
          {/* while a job runs it owns the flag (the backend answers 409) */}
          {s.readonly_flag && s.mode !== "tiqora" && s.active_job_id == null && (
            <div className="flex items-center gap-3">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => releaseLock.mutate()}
                disabled={releaseLock.isPending}
                data-testid="itsm-import-release-lock"
              >
                {t("admin.itsmImport.releaseLock")}
              </Button>
              {releaseLock.isError && (
                <span className="text-sm text-danger" role="alert">
                  {apiErrorMessage(releaseLock.error, t("admin.itsmImport.releaseLockError"))}
                </span>
              )}
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              onClick={() => start.mutate(true)}
              disabled={!canImport || busy}
              title={reason}
              data-testid="itsm-import-dry-run"
            >
              {t("admin.itsmImport.dryRun")}
            </Button>
            <Button
              variant="primary"
              onClick={() => setConfirming(true)}
              disabled={!canImport || busy}
              title={reason}
              data-testid="itsm-import-start"
            >
              {t("admin.itsmImport.import")}
            </Button>
            {inModeA && (
              <Button
                variant="secondary"
                onClick={() => setSwitching(true)}
                disabled={!s.can_switch || busy}
                title={switchReason}
                data-testid="itsm-switch-open"
              >
                {t("admin.itsmImport.switch.open")}
              </Button>
            )}
          </div>
          {inModeA && switchReason && switchReason !== reason && (
            <p className="text-sm text-muted" data-testid="itsm-switch-blocked">
              {switchReason}
            </p>
          )}
          {start.isError && (
            <p className="text-sm text-danger" role="alert" data-testid="itsm-import-start-error">
              {apiErrorMessage(start.error, t("admin.itsmImport.startError"))}
            </p>
          )}
        </>
      )}

      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-ink">{t("admin.itsmImport.runs")}</h2>
        {runsQ.isLoading && <Spinner />}
        {runsQ.data && runsQ.data.length === 0 && (
          <p className="text-sm text-muted">{t("admin.itsmImport.noRun")}</p>
        )}
        {runsQ.data && runsQ.data.length > 0 && (
          <table className="w-full text-left text-sm" data-testid="itsm-import-runs">
            <thead className="text-xs text-muted">
              <tr>
                <th className="py-1 pr-3 font-medium">{t("admin.itsmImport.col.kind")}</th>
                <th className="px-2 py-1 font-medium">{t("admin.itsmImport.col.started")}</th>
                <th className="px-2 py-1 font-medium">{t("admin.itsmImport.col.by")}</th>
                <th className="px-2 py-1 font-medium">{t("admin.itsmImport.col.status")}</th>
                <th className="px-2 py-1 font-medium" />
              </tr>
            </thead>
            <tbody>
              {runsQ.data.map((r) => (
                <RunRows
                  key={r.id}
                  run={r}
                  open={openRun === r.id}
                  onToggle={() => setOpenRun(openRun === r.id ? null : r.id)}
                  locale={locale}
                />
              ))}
            </tbody>
          </table>
        )}
      </section>

      <ConfirmDialog
        open={confirming}
        title={t("admin.itsmImport.confirmTitle")}
        message={t("admin.itsmImport.confirmMessage")}
        confirmLabel={t("admin.itsmImport.import")}
        onConfirm={() => start.mutate(false)}
        onCancel={() => setConfirming(false)}
        pending={busy}
      />

      <SwitchDialog
        open={switching}
        onClose={() => setSwitching(false)}
        onStarted={(id) => {
          setSwitchJobId(id);
          refresh();
        }}
        withoutCiData={withoutCiData}
      />
      <RollbackDialog open={rollingBack} onClose={() => setRollingBack(false)} />
      <JobDialog
        jobId={switchJobId}
        title={t("admin.itsmImport.switch.jobTitle")}
        testId="switch-job-dialog"
        onClose={() => {
          setSwitchJobId(null);
          refresh();
        }}
        onSucceeded={refresh}
      />
      <JobDialog
        jobId={jobId}
        title={t("jobs.kinds.core.itsm_import")}
        onClose={() => {
          setJobId(null);
          refresh();
        }}
        onSucceeded={refresh}
      />
    </div>
  );
}

function RunRows({
  run,
  open,
  onToggle,
  locale,
}: {
  run: ImportRunOut;
  open: boolean;
  onToggle: () => void;
  locale: string;
}) {
  const { t } = useTranslation();
  return (
    <>
      <tr className="border-t border-hairline" data-testid={`itsm-import-run-${run.id}`}>
        <td className="py-1.5 pr-3 text-ink">{t(`admin.itsmImport.kind.${run.kind}`)}</td>
        <td className="px-2 py-1.5">{formatDateTime(run.started_at, locale)}</td>
        <td className="px-2 py-1.5">{run.started_by_name ?? "—"}</td>
        <td className="px-2 py-1.5">{t(`admin.itsmImport.status.${run.status}`)}</td>
        <td className="px-2 py-1.5 text-right">
          <Button variant="secondary" size="sm" onClick={onToggle} aria-expanded={open}>
            {t("admin.itsmImport.col.report")}
          </Button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={5} className="pb-3">
            <ImportRunReport run={run} />
          </td>
        </tr>
      )}
    </>
  );
}
