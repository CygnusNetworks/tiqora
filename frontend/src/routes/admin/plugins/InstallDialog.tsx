import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SoftChip } from "@/components/ui/StatusChip";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { apiErrorMessage } from "@/lib/avatarApi";
import { pluginsApi, type PluginPackage } from "@/lib/pluginsApi";
import { capabilityText } from "./capabilities";
import { RestartPanel } from "./RestartPanel";
import { trustColor, trustLabelKey } from "./trust";

const MAX_LISTED_TABLES = 3;

function Checkbox({
  checked,
  onChange,
  children,
  testId,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  children: string;
  testId?: string;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-ink">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} data-testid={testId} />
      {children}
    </label>
  );
}

/** Report of an uploaded or downloaded package, with the options to install it and the job's progress. */
export function InstallDialog({
  pkg,
  open,
  onClose,
  coordinatedRestart,
}: {
  pkg: PluginPackage;
  open: boolean;
  onClose: () => void;
  coordinatedRestart: boolean;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [enable, setEnable] = useState(true);
  const [restart, setRestart] = useState(true);
  const [trusted, setTrusted] = useState(false);
  const [jobId, setJobId] = useState<number | null>(null);
  const [restartBefore, setRestartBefore] = useState(0);
  const wantsRestart = coordinatedRestart && restart;
  const isNew = pkg.action === "install";

  const installM = useMutation({
    mutationFn: async () => {
      let before = 0;
      if (wantsRestart) {
        // The generation of the restart before ours, so the panel only follows the new one.
        try {
          before = (await pluginsApi.restartStatus())?.generation ?? 0;
        } catch {
          before = 0;
        }
      }
      const accepted = await pluginsApi.installPackage(pkg.sha256, {
        enable: isNew ? enable : true,
        restart: wantsRestart,
      });
      return { accepted, before };
    },
    onSuccess: ({ accepted, before }) => {
      setRestartBefore(before);
      setJobId(accepted.job_id);
    },
  });
  const { job, isTerminal, cancel, isCancelling } = useJob(jobId);

  const succeeded = job?.status === "succeeded";
  const started = jobId != null;
  const needsTrust = pkg.trust === "unsigned";
  const canInstall = pkg.installable && pkg.problems.length === 0 && (!needsTrust || trusted) && !installM.isPending;

  const tables = pkg.capabilities.filter((c) => c.kind === "table");
  const manyTables = tables.length > MAX_LISTED_TABLES;
  const lines = pkg.capabilities.filter((c) => !(manyTables && c.kind === "table"));

  const finish = () => {
    if (isTerminal) {
      void qc.invalidateQueries({ queryKey: ["admin", "plugins"] });
      void qc.invalidateQueries({ queryKey: ["plugins", "frontend"] });
    }
    onClose();
  };

  const from = pkg.installed_version ?? "";
  const actionLine =
    pkg.action === "update"
      ? t("admin.plugins.install.actionUpdate", { from, to: pkg.version })
      : pkg.action === "downgrade"
        ? t("admin.plugins.install.actionDowngrade", { from, to: pkg.version })
        : pkg.action === "reinstall"
          ? t("admin.plugins.install.actionReinstall")
          : pkg.action === "install"
            ? t("admin.plugins.install.actionInstall")
            : null;

  const footer = started ? (
    <Button variant="secondary" onClick={finish} data-testid="plugin-install-close">
      {t("common.close")}
    </Button>
  ) : (
    <>
      <Button variant="secondary" onClick={onClose}>
        {t("common.cancel")}
      </Button>
      <Button variant="primary" disabled={!canInstall} onClick={() => installM.mutate()} data-testid="plugin-install-submit">
        {t("admin.plugins.install.submit")}
      </Button>
    </>
  );

  return (
    <Dialog open={open} onClose={finish} title={t("admin.plugins.install.title")} size="xl" footer={footer}>
      <div className="space-y-4 text-sm" data-testid="plugin-install-dialog">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-display text-base font-semibold text-ink">{pkg.name}</h3>
            <SoftChip color={trustColor(pkg.trust)} data-testid="plugin-install-trust">
              {t(trustLabelKey(pkg.trust))}
            </SoftChip>
          </div>
          <p className="mt-0.5 text-xs text-muted">
            {t("admin.plugins.version", { version: pkg.version })} · {t("admin.plugins.publisher", { publisher: pkg.publisher })}
          </p>
          {pkg.signer && <p className="text-xs text-muted">{t("admin.plugins.trust.signedBy", { signer: pkg.signer })}</p>}
          {pkg.description && <p className="mt-1.5 text-ink">{pkg.description}</p>}
          {actionLine && <p className="mt-1.5 font-medium text-ink">{actionLine}</p>}
        </div>

        {pkg.changelog && (
          <details>
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.plugins.install.changelog")}
            </summary>
            <p className="mt-1 whitespace-pre-wrap text-ink">{pkg.changelog}</p>
          </details>
        )}

        <section>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
            {t("admin.plugins.capabilities.title")}
          </h4>
          {pkg.capabilities.length === 0 ? (
            <p className="text-muted">{t("admin.plugins.capabilities.none")}</p>
          ) : (
            <ul className="list-disc space-y-0.5 pl-5 text-ink">
              {lines.map((cap) => (
                <li key={`${cap.kind}:${cap.value}`}>{capabilityText(t, cap)}</li>
              ))}
              {manyTables && (
                <li>
                  <details>
                    <summary className="cursor-pointer">
                      {t("admin.plugins.capabilities.tables", { count: tables.length })}
                    </summary>
                    <ul className="mt-1 list-none pl-0 font-mono text-xs text-muted">
                      {tables.map((c) => (
                        <li key={c.value}>{c.value}</li>
                      ))}
                    </ul>
                  </details>
                </li>
              )}
            </ul>
          )}
        </section>

        {pkg.problems.length > 0 && (
          <div role="alert" className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2" data-testid="plugin-install-problems">
            <p className="font-semibold text-danger">{t("admin.plugins.install.problems")}</p>
            <ul className="mt-1 list-disc pl-5 text-ink">
              {pkg.problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </div>
        )}
        {pkg.warnings.length > 0 && (
          <div className="rounded-lg border border-amber/40 bg-amber/10 px-3 py-2" data-testid="plugin-install-warnings">
            <p className="font-semibold text-ink">{t("admin.plugins.install.warnings")}</p>
            <ul className="mt-1 list-disc pl-5 text-ink">
              {pkg.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </div>
        )}

        <p className="rounded-lg border border-hairline bg-surface-subtle px-3 py-2 text-ink" data-testid="plugin-install-trust-note">
          {t("admin.plugins.install.trustNote")}
        </p>

        {!started && (
          <div className="space-y-2">
            {isNew && (
              <Checkbox checked={enable} onChange={setEnable} testId="plugin-install-enable">
                {t("admin.plugins.install.enable")}
              </Checkbox>
            )}
            {coordinatedRestart && (
              <Checkbox checked={restart} onChange={setRestart} testId="plugin-install-restart">
                {t("admin.plugins.install.restart")}
              </Checkbox>
            )}
            {needsTrust && (
              <Checkbox checked={trusted} onChange={setTrusted} testId="plugin-install-trust-check">
                {t("admin.plugins.install.trustUnsigned")}
              </Checkbox>
            )}
          </div>
        )}

        {installM.isError && (
          <p role="alert" className="text-danger" data-testid="plugin-install-error">
            {apiErrorMessage(installM.error, t("admin.plugins.actionError"))}
          </p>
        )}
        {started && (
          <div className="space-y-3" data-testid="plugin-install-job">
            <p className="font-medium text-ink">
              {succeeded ? t("admin.plugins.install.done") : t("admin.plugins.install.running")}
            </p>
            {job && <JobProgress job={job} onCancel={cancel} cancelling={isCancelling} testId="plugin-install-progress" />}
            {succeeded && (!coordinatedRestart || restart) && (
              <RestartPanel generationHint={wantsRestart ? restartBefore : undefined} />
            )}
          </div>
        )}
      </div>
    </Dialog>
  );
}
