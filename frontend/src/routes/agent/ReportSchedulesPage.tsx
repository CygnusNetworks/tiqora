import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { fromSchedule, toUpdateRequest } from "@/components/reports/schedules/scheduleForm";
import { SchedulesGate } from "@/components/reports/schedules/SchedulesGate";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { toBcp47 } from "@/i18n/locales";
import { ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { reportErrorMessage } from "@/lib/reportsApi";
import { reportSchedulesApi, type ReportScheduleListItem } from "@/lib/reportSchedulesApi";

const linkBtnCls =
  "inline-flex items-center rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:border-accent/60 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

const STATUS_TONE = { succeeded: "success", partial: "warn", failed: "danger", skipped: "muted" } as const;

function itemTitle(i: ReportScheduleListItem["items"][number], t: (k: string) => string): string {
  return i.title ?? (i.title_key ? t(i.title_key) : i.report_key);
}

function ListContent() {
  const { t, i18n } = useTranslation();
  const bcp = toBcp47(i18n.resolvedLanguage ?? i18n.language ?? "de");
  const { user } = useAuth();
  const qc = useQueryClient();
  const isAdmin = user?.is_admin === true;
  const [all, setAll] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: ["report-schedules", "list", all],
    queryFn: ({ signal }) => reportSchedulesApi.list(all, signal),
  });

  // The list rows are lightweight: the full schedule is loaded to send it back with its version.
  const toggle = useMutation({
    mutationFn: async ({ id, active }: { id: number; active: boolean }) => {
      const full = await reportSchedulesApi.get(id);
      return reportSchedulesApi.update(id, toUpdateRequest({ ...fromSchedule(full), active }));
    },
    onSuccess: () => {
      setMessage(null);
      void qc.invalidateQueries({ queryKey: ["report-schedules"] });
    },
    onError: (err) => {
      setMessage(
        err instanceof ApiError && err.status === 409
          ? t("reports.schedules.staleVersion")
          : reportErrorMessage(err, t("reports.schedules.saveFailed")),
      );
      void qc.invalidateQueries({ queryKey: ["report-schedules"] });
    },
  });

  const rows = listQ.data ?? [];

  return (
    <div className="space-y-5">
      <Link to="/agent/reports" className="inline-flex items-center gap-1 text-xs text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
        <span aria-hidden>←</span> {t("reports.back")}
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold tracking-tight text-ink">{t("reports.schedules.title")}</h1>
          <p className="mt-1 text-sm text-muted">{t("reports.schedules.subtitle")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          {isAdmin && (
            <label className="flex items-center gap-2 text-sm text-ink">
              <Switch checked={all} onChange={setAll} testId="schedules-show-all" aria-label={t("reports.schedules.showAll")} />
              {t("reports.schedules.showAll")}
            </label>
          )}
          <Link to="/agent/reports/schedules/new" className={linkBtnCls} data-testid="schedules-create">
            {t("reports.schedules.create")}
          </Link>
        </div>
      </div>

      {message && (
        <p className="text-sm text-danger" role="alert" data-testid="schedules-message">
          {message}
        </p>
      )}

      {listQ.isPending ? (
        <Spinner />
      ) : listQ.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="schedules-error">
          {reportErrorMessage(listQ.error, t("reports.schedules.loadFailed"))}
        </p>
      ) : rows.length === 0 ? (
        <div className="space-y-3 rounded-lg border border-hairline bg-surface px-4 py-10 text-center" data-testid="schedules-empty">
          <p className="text-sm text-muted">{t("reports.schedules.empty")}</p>
          <Link to="/agent/reports/schedules/new" className={linkBtnCls}>
            {t("reports.schedules.create")}
          </Link>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-hairline">
          <table className="w-full min-w-[52rem] text-left text-sm" data-testid="schedules-table">
            <thead className="bg-surface-subtle text-xs text-muted">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">{t("reports.schedules.name")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("reports.schedules.items")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("reports.schedules.recurrence")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("reports.schedules.nextRun")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("reports.schedules.lastRun")}</th>
                {all && <th scope="col" className="px-3 py-2 font-medium">{t("reports.schedules.owner")}</th>}
                <th scope="col" className="px-3 py-2 font-medium">{t("reports.schedules.active")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {rows.map((s) => (
                <tr key={s.id} className="align-top" data-testid={`schedule-row-${s.id}`}>
                  <td className="px-3 py-2">
                    <Link
                      to="/agent/reports/schedules/$scheduleId"
                      params={{ scheduleId: String(s.id) }}
                      className="font-medium text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                    >
                      {s.name}
                    </Link>
                    <span className="mt-0.5 block text-xs uppercase text-muted">{s.format}</span>
                  </td>
                  <td className="px-3 py-2 text-ink">{s.items.map((i) => itemTitle(i, t)).join(", ")}</td>
                  <td className="px-3 py-2 text-ink">{s.recurrence_text}</td>
                  <td className="px-3 py-2 text-ink">
                    {s.active ? formatDateTime(s.next_run_at, bcp) : <Badge tone="warn">{t("reports.schedules.paused")}</Badge>}
                    {s.paused_reason && (
                      <span className="mt-1 block text-xs text-muted" data-testid={`schedule-row-${s.id}-reason`}>
                        {t("reports.schedules.pausedReason", { reason: s.paused_reason })}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-ink">
                    {s.last_run_at ? formatDateTime(s.last_run_at, bcp) : "—"}
                    {s.last_status && (
                      <Badge tone={STATUS_TONE[s.last_status]} className="ml-2">
                        {t(`reports.schedules.status.${s.last_status}`)}
                      </Badge>
                    )}
                  </td>
                  {all && <td className="px-3 py-2 text-ink">{s.owner.name}</td>}
                  <td className="px-3 py-2">
                    <Switch
                      checked={s.active}
                      disabled={!s.can_edit || toggle.isPending}
                      onChange={(active) => toggle.mutate({ id: s.id, active })}
                      testId={`schedule-toggle-${s.id}`}
                      aria-label={`${t("reports.schedules.active")}: ${s.name}`}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** `/agent/reports/schedules`: own schedules (admins: all), with the active switch. */
export function ReportSchedulesPage() {
  return (
    <SchedulesGate>
      <div className="mx-auto w-full max-w-6xl px-4 py-6" data-testid="report-schedules-page">
          <ListContent />
        </div>
      </SchedulesGate>
  );
}
