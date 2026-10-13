import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { apiErrorMessage } from "@/lib/avatarApi";
import { jobsApi } from "@/lib/jobsApi";
import { pluginsApi, type PluginAdmin } from "@/lib/pluginsApi";
import { settingsSectionsApi } from "@/lib/settingsSectionsApi";
import { RestartPanel } from "./RestartPanel";
import { jobRestartGeneration } from "./restartGeneration";

/** Backup retention of the job system when the setting cannot be read (`jobs.retention_days`). */
const DEFAULT_RETENTION_DAYS = 14;

function retentionDays(values: Record<string, unknown> | undefined): number {
  const v = values?.retention_days;
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : DEFAULT_RETENTION_DAYS;
}

/** Removes an installed plugin: keep or delete its data (with a backup to download), then follow the job. */
export function UninstallDialog({
  plugin,
  open,
  onClose,
  coordinatedRestart,
}: {
  plugin: PluginAdmin;
  open: boolean;
  onClose: () => void;
  coordinatedRestart: boolean;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [deleteData, setDeleteData] = useState(false);
  const [restart, setRestart] = useState(true);
  const [jobId, setJobId] = useState<number | null>(null);
  const wantsRestart = coordinatedRestart && restart;

  // The backup is only kept for the job system's retention time; admins treat it as the only copy (F22).
  const retentionQ = useQuery({
    queryKey: ["admin", "settings-sections", "jobs"],
    queryFn: ({ signal }) => settingsSectionsApi.get("jobs", signal),
    retry: false,
  });
  const days = retentionDays(retentionQ.data?.values as Record<string, unknown> | undefined);

  const uninstallM = useMutation({
    mutationFn: () => pluginsApi.uninstall(plugin.id, { delete_data: deleteData, restart: wantsRestart }),
    onSuccess: (accepted) => setJobId(accepted.job_id),
  });
  const { job, isTerminal, cancel, isCancelling } = useJob(jobId);
  const started = jobId != null;
  const succeeded = job?.status === "succeeded";
  const failed = isTerminal && !succeeded;

  const finish = () => {
    // also when closed early: the job goes on, and a second "Deinstallieren" would get a 409
    if (started) {
      void qc.invalidateQueries({ queryKey: ["admin", "plugins"] });
      void qc.invalidateQueries({ queryKey: ["plugins", "frontend"] });
    }
    onClose();
  };

  const footer = started ? (
    <Button variant="secondary" onClick={finish} data-testid="plugin-uninstall-close">
      {t("common.close")}
    </Button>
  ) : (
    <>
      <Button variant="secondary" onClick={onClose}>
        {t("common.cancel")}
      </Button>
      <Button
        variant="danger"
        disabled={uninstallM.isPending}
        onClick={() => uninstallM.mutate()}
        data-testid="plugin-uninstall-submit"
      >
        {t("admin.plugins.uninstall.submit")}
      </Button>
    </>
  );

  return (
    <Dialog open={open} onClose={finish} title={t("admin.plugins.uninstall.title")} size="lg" footer={footer}>
      <div className="space-y-4 text-sm" data-testid="plugin-uninstall-dialog">
        <p className="font-medium text-ink">{plugin.name}</p>
        {!started && (
          <>
            <fieldset className="space-y-2">
              <legend className="sr-only">{t("admin.plugins.uninstall.dataLegend")}</legend>
              <label className="flex items-start gap-2 text-ink">
                <input
                  type="radio"
                  name="plugin-uninstall-data"
                  checked={!deleteData}
                  onChange={() => setDeleteData(false)}
                  className="mt-0.5"
                />
                <span>
                  {t("admin.plugins.uninstall.keepData")}
                  <span className="block text-xs text-muted">{t("admin.plugins.uninstall.keepHint")}</span>
                </span>
              </label>
              <label className="flex items-start gap-2 text-ink">
                <input
                  type="radio"
                  name="plugin-uninstall-data"
                  checked={deleteData}
                  onChange={() => setDeleteData(true)}
                  className="mt-0.5"
                />
                <span>
                  {t("admin.plugins.uninstall.deleteData")}
                  <span className="block text-xs text-muted" data-testid="plugin-uninstall-delete-hint">
                    {t("admin.plugins.uninstall.deleteHint", { count: days })}
                  </span>
                </span>
              </label>
            </fieldset>
            {coordinatedRestart && (
              <label className="flex items-center gap-2 text-ink">
                <input
                  type="checkbox"
                  checked={restart}
                  onChange={(e) => setRestart(e.target.checked)}
                  data-testid="plugin-uninstall-restart"
                />
                {t("admin.plugins.install.restart")}
              </label>
            )}
          </>
        )}
        {uninstallM.isError && (
          <p role="alert" className="text-danger" data-testid="plugin-uninstall-error">
            {apiErrorMessage(uninstallM.error, t("admin.plugins.actionError"))}
          </p>
        )}
        {started && (
          <div className="space-y-3" data-testid="plugin-uninstall-job">
            <p className={failed ? "font-medium text-danger" : "font-medium text-ink"} data-testid="plugin-uninstall-headline">
              {succeeded
                ? t("admin.plugins.uninstall.done")
                : failed
                  ? t("admin.plugins.uninstall.failed")
                  : t("admin.plugins.uninstall.running")}
            </p>
            {job && <JobProgress job={job} onCancel={cancel} cancelling={isCancelling} testId="plugin-uninstall-progress" />}
            {succeeded && job.files.length > 0 && (
              <div className="rounded-lg border border-hairline bg-surface-subtle px-3 py-2" data-testid="plugin-uninstall-backup">
                <p className="text-ink">{t("admin.plugins.uninstall.backupNote", { count: days })}</p>
                <ul className="mt-1 space-y-0.5">
                  {job.files.map((file) => (
                    <li key={file.id}>
                      <a
                        href={jobsApi.fileUrl(job.id, file.id)}
                        download
                        className="text-accent underline"
                        data-testid={`plugin-uninstall-backup-${file.id}`}
                      >
                        {t("admin.plugins.uninstall.backupDownload", { name: file.name })}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {succeeded && (!coordinatedRestart || restart) && (
              <RestartPanel generation={wantsRestart ? jobRestartGeneration(job.result) : null} />
            )}
          </div>
        )}
      </div>
    </Dialog>
  );
}
