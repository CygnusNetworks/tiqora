import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Menu, MenuItem } from "@/components/ui/Menu";
import { Spinner } from "@/components/ui/Spinner";
import { toBcp47 } from "@/i18n/locales";
import { ApiError } from "@/lib/api";
import { formatBytes, formatDateTime } from "@/lib/format";
import { reportErrorMessage } from "@/lib/reportsApi";
import { reportSchedulesApi, type ReportRun } from "@/lib/reportSchedulesApi";
import { RUN_POLL_MS, runIsActive } from "./scheduleUi";

const RUN_LIMIT = 20;

const TONES: Record<ReportRun["status"], "default" | "success" | "warn" | "danger" | "muted" | "accent"> = {
  queued: "muted",
  running: "accent",
  succeeded: "success",
  partial: "warn",
  failed: "danger",
  skipped: "muted",
};

/**
 * Run history of one schedule (newest first) with the "Jetzt senden" menu. While a run is
 * queued or running the list is asked again every 5 s.
 */
export function ScheduleRunsTable({ scheduleId, canRun }: { scheduleId: number; canRun: boolean }) {
  const { t, i18n } = useTranslation();
  const bcp = toBcp47(i18n.resolvedLanguage ?? i18n.language ?? "de");
  const qc = useQueryClient();
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const runsQ = useQuery({
    queryKey: ["report-schedules", scheduleId, "runs"],
    queryFn: ({ signal }) => reportSchedulesApi.runs(scheduleId, RUN_LIMIT, signal),
    refetchInterval: (q) => (q.state.data?.some(runIsActive) ? RUN_POLL_MS : false),
  });

  const runNow = useMutation({
    mutationFn: (ownerOnly: boolean) => reportSchedulesApi.runNow(scheduleId, ownerOnly),
    onSuccess: () => {
      setNotice({ tone: "ok", text: t("reports.schedules.runStarted") });
      void qc.invalidateQueries({ queryKey: ["report-schedules", scheduleId, "runs"] });
    },
    onError: (err) =>
      setNotice({
        tone: "error",
        text:
          err instanceof ApiError && err.status === 409
            ? t("reports.schedules.runBusy")
            : reportErrorMessage(err, t("reports.schedules.runFailed")),
      }),
  });

  const runs = runsQ.data ?? [];
  const busy = runNow.isPending || runs.some(runIsActive);

  return (
    <section className="space-y-3" aria-labelledby="schedule-runs-title" data-testid="schedule-runs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="schedule-runs-title" className="text-sm font-semibold text-ink">
          {t("reports.schedules.history")}
        </h2>
        {canRun && (
          <Menu
            align="right"
            panelTestId="schedule-run-menu"
            trigger={({ ref, toggleProps }) => (
              <button
                type="button"
                ref={ref}
                {...toggleProps}
                disabled={runNow.isPending}
                className="inline-flex items-center gap-1 rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-60"
                data-testid="schedule-run-now"
              >
                {t("reports.schedules.runNow")}
                <span aria-hidden>▾</span>
              </button>
            )}
          >
            <MenuItem testId="schedule-run-test" onSelect={() => runNow.mutate(true)} disabled={busy}>
              {t("reports.schedules.testSend")}
            </MenuItem>
            <MenuItem testId="schedule-run-all" onSelect={() => runNow.mutate(false)} disabled={busy}>
              {t("reports.schedules.sendToAll")}
            </MenuItem>
          </Menu>
        )}
      </div>

      {notice && (
        <p
          className={notice.tone === "ok" ? "text-sm text-ink" : "text-sm text-danger"}
          role={notice.tone === "ok" ? "status" : "alert"}
          data-testid="schedule-run-notice"
        >
          {notice.text}
        </p>
      )}

      {runsQ.isPending ? (
        <Spinner />
      ) : runsQ.isError ? (
        <p className="text-sm text-danger" role="alert">
          {t("reports.schedules.loadFailed")}
        </p>
      ) : runs.length === 0 ? (
        <p className="text-sm text-muted" data-testid="schedule-runs-empty">
          {t("reports.schedules.noRuns")}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-hairline">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <thead className="bg-surface-subtle text-xs text-muted">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">{t("reports.schedules.runTime")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("reports.schedules.runStatus")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("reports.schedules.runResult")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("reports.schedules.files")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {runs.map((r) => (
                <tr key={r.id} data-testid={`schedule-run-${r.id}`} className="align-top">
                  <td className="px-3 py-2 text-ink">
                    {formatDateTime(r.finished_at ?? r.started_at ?? r.due_at ?? r.created_at, bcp)}
                    <span className="block text-xs text-muted">{t(`reports.schedules.kind.${r.run_kind}`)}</span>
                  </td>
                  <td className="px-3 py-2">
                    <Badge tone={TONES[r.status]} data-testid={`schedule-run-${r.id}-status`}>
                      {t(`reports.schedules.status.${r.status}`)}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 text-muted">
                    <span className="flex flex-wrap gap-x-3">
                      {r.row_count != null && <span>{t("reports.schedules.rows", { count: r.row_count })}</span>}
                      {r.recipient_count != null && (
                        <span>{t("reports.schedules.recipientCount", { count: r.recipient_count })}</span>
                      )}
                    </span>
                    {r.error && <span className="block text-danger">{r.error}</span>}
                    {r.detail && <span className="block">{r.detail}</span>}
                  </td>
                  <td className="px-3 py-2">
                    {r.files.length === 0 ? (
                      <span className="text-muted">—</span>
                    ) : (
                      <ul className="space-y-0.5">
                        {r.files.map((f) => (
                          <li key={f.id}>
                            <a
                              href={reportSchedulesApi.fileUrl(r.id, f.id)}
                              download
                              className="text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                              aria-label={`${t("reports.schedules.download")}: ${f.name}`}
                              data-testid={`schedule-run-file-${f.id}`}
                            >
                              {f.name}
                            </a>{" "}
                            <span className="text-xs text-muted">{formatBytes(f.size_bytes)}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
