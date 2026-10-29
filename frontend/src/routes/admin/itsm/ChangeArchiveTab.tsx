import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { changeAdminErrorText } from "@/components/itsm/change/changeAdminUi";
import { utcText } from "@/components/itsm/change/changeUi";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { Button } from "@/components/ui/Button";
import { changeAdminApi, changeAdminKeys } from "@/lib/changeAdminApi";
import { changeKeys } from "@/lib/changeApi";

const COUNTS = ["created", "updated", "unchanged", "skipped"] as const;
type ArchiveResult = Record<(typeof COUNTS)[number], number> & { dry_run: boolean; not_archived: Record<string, number> };

/** The result of `core.itsm_legacy_change_archive` (`{dry_run, created, …, not_archived}`), or null. */
function archiveResult(raw: unknown): ArchiveResult | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (!COUNTS.every((k) => typeof r[k] === "number")) return null;
  const notArchived = r.not_archived && typeof r.not_archived === "object" ? (r.not_archived as Record<string, unknown>) : {};
  return {
    dry_run: r.dry_run === true,
    created: r.created as number,
    updated: r.updated as number,
    unchanged: r.unchanged as number,
    skipped: r.skipped as number,
    not_archived: Object.fromEntries(Object.entries(notArchived).filter((e): e is [string, number] => typeof e[1] === "number" && e[1] > 0)),
  };
}

type Followed = { jobId: number; joined: boolean; wasActive: boolean };

/**
 * The Znuny change archive: how many changes are archived, a dry run and a real run as a background
 * job (`POST /legacy-archive` → `GET /jobs/{id}`), its progress and counts. While a run is active it
 * is followed instead of starting another; when the start is answered with a run that was already
 * going (the backend keeps one active run per kind), the page says so.
 */
export function ChangeArchiveTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: changeAdminKeys.legacyArchive(), queryFn: ({ signal }) => changeAdminApi.legacyArchive(signal) });
  const [followed, setFollowed] = useState<Followed | null>(null);
  const activeId = status.data?.active_job_id ?? null;
  useEffect(() => {
    if (activeId != null) setFollowed((f) => f ?? { jobId: activeId, joined: false, wasActive: true });
  }, [activeId]);

  const start = useMutation({
    mutationFn: (dryRun: boolean) => changeAdminApi.startLegacyArchive({ dry_run: dryRun }),
    onSuccess: (accepted) =>
      // A new job starts as "queued"; "running" or the id of the run we knew means it was already going.
      setFollowed({ jobId: accepted.job_id, joined: accepted.status === "running" || accepted.job_id === activeId, wasActive: false }),
  });
  const { job, isTerminal, cancel, isCancelling } = useJob(followed?.jobId ?? null);
  useEffect(() => {
    if (isTerminal) {
      void qc.invalidateQueries({ queryKey: changeAdminKeys.legacyArchive() });
      void qc.invalidateQueries({ queryKey: changeKeys.all() });
    }
  }, [isTerminal, qc]);

  const running = followed != null && !isTerminal;
  const canStart = status.data?.can_start === true && !running && !start.isPending;
  const result = job?.status === "succeeded" ? archiveResult(job.result) : null;
  const data = status.data;

  return (
    <section className="space-y-4" data-testid="change-archive-tab">
      <div className="space-y-1 text-sm text-muted">
        <p>{t("admin.itsmChange.archive.intro")}</p>
        <p>{t("admin.itsmChange.archive.searchHint")}</p>
      </div>
      {status.isError && (
        <p className="text-sm text-danger" role="alert" data-testid="archive-status-error">
          {changeAdminErrorText(status.error, t)}
        </p>
      )}
      {data && (
        <div className="space-y-3 rounded-md border border-hairline bg-surface p-3 text-sm">
          <p className="font-medium text-ink" data-testid="archive-count">
            {t("admin.itsmChange.archive.archived", { count: data.archived })}
          </p>
          <p className="text-xs text-muted" data-testid="archive-last">
            {data.last_archived_at ? t("admin.itsmChange.archive.lastRun", { time: utcText(data.last_archived_at) }) : t("admin.itsmChange.archive.never")}
          </p>
          {!data.can_start && (
            <p className="text-sm text-escalation" role="status" data-testid="archive-blocked">
              {data.blocked_code
                ? t(`itsm.change.errors.${data.blocked_code}`, { defaultValue: data.blocked_message ?? "" })
                : data.blocked_message}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={!canStart} onClick={() => start.mutate(true)} data-testid="archive-dry-run">
              {t("admin.itsmChange.archive.dryRun")}
            </Button>
            <Button variant="primary" disabled={!canStart} onClick={() => start.mutate(false)} data-testid="archive-run">
              {t("admin.itsmChange.archive.run")}
            </Button>
            <span className="text-xs text-muted">{t("admin.itsmChange.archive.dryRunHint")}</span>
          </div>
        </div>
      )}
      {start.isError && (
        <p className="text-sm text-danger" role="alert" data-testid="archive-error">
          {changeAdminErrorText(start.error, t)}
        </p>
      )}

      {followed?.wasActive && running && (
        <p className="text-sm text-ink" role="status" data-testid="archive-active">
          {t("admin.itsmChange.archive.active")}
        </p>
      )}
      {followed?.joined && (
        <p className="text-sm text-ink" role="status" data-testid="archive-joined">
          {t("admin.itsmChange.archive.joined")}
        </p>
      )}
      {job && <JobProgress job={job} onCancel={cancel} cancelling={isCancelling} testId="archive-job" />}
      {result && (
        <div className="space-y-2 rounded-md border border-hairline bg-surface p-3 text-sm" data-testid="archive-result">
          <h3 className="font-semibold text-ink">
            {result.dry_run ? t("admin.itsmChange.archive.resultTitleDry") : t("admin.itsmChange.archive.resultTitle")}
          </h3>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
            {COUNTS.map((k) => (
              <div key={k} className="min-w-0" data-testid={`archive-result-${k}`}>
                <dt className="text-xs text-muted">{t(`admin.itsmChange.archive.${k}`)}</dt>
                <dd className="font-mono tabular-nums text-ink">{result[k]}</dd>
              </div>
            ))}
          </dl>
          {Object.keys(result.not_archived).length > 0 && (
            <div className="text-xs text-muted" data-testid="archive-not-archived">
              <p>{t("admin.itsmChange.archive.notArchived")}</p>
              <ul className="list-inside list-disc">
                {Object.entries(result.not_archived).map(([kind, n]) => (
                  <li key={kind}>
                    {t(`admin.itsmChange.archive.notArchivedKinds.${kind}`, { defaultValue: kind })}: {n}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
