import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiErrorMessage } from "@/lib/avatarApi";
import { deployStatusApi } from "@/lib/deployStatusApi";
import { processDesignerApi, type ValidationIssue } from "@/lib/processDesignerApi";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { DEPLOY_STATUS_QUERY_KEY, DeployStatusCard } from "@/components/process/DeployStatusCard";

/**
 * "Bereitstellen" for the designer: checks the process first (a confirm dialog with the error
 * count when it has errors), deploys, then shows the Znuny sync state with polling. The button is
 * only there where the server reports a `deploy_mode` for processes.
 */
export function DeployBar({
  processEntityId,
  disabled,
  onValidated,
}: {
  processEntityId: string;
  /** True while there are unsaved edits: they would not be part of the deployment. */
  disabled?: boolean;
  /** Fresh check result, so the page can show it without a second request. */
  onValidated?: (issues: ValidationIssue[]) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { confirm, dialog } = useConfirm();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const statusQ = useQuery({
    queryKey: DEPLOY_STATUS_QUERY_KEY,
    queryFn: ({ signal }) => deployStatusApi.status(signal),
  });
  const canDeploy = statusQ.data?.process.deploy_mode != null;

  const deploy = async () => {
    setBusy(true);
    setError(null);
    try {
      let issues: ValidationIssue[];
      try {
        issues = await processDesignerApi.validation(processEntityId);
      } catch (err) {
        setError(apiErrorMessage(err, t("admin.processDesigner.deploy.checkFailed")));
        return;
      }
      onValidated?.(issues);
      const errors = issues.filter((i) => i.severity === "error").length;
      if (errors > 0) {
        const ok = await confirm({
          title: t("admin.processDesigner.deploy.confirmTitle"),
          message: t("admin.processDesigner.deploy.confirm", { count: errors }),
          confirmLabel: t("admin.deploy.deployNow"),
          variant: "danger",
        });
        if (!ok) return;
      }
      try {
        await deployStatusApi.deploy("process");
      } catch (err) {
        setError(apiErrorMessage(err, t("admin.deploy.deployFailed")));
        return;
      }
      await Promise.all([
        qc.invalidateQueries({ queryKey: DEPLOY_STATUS_QUERY_KEY }),
        qc.invalidateQueries({ queryKey: ["admin", "process-designer"] }),
        qc.invalidateQueries({ queryKey: ["admin", "processes"] }),
      ]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2" data-testid="deploy-bar">
      <div className="flex flex-wrap items-center gap-3">
        {canDeploy ? (
          <Button
            size="sm"
            variant="primary"
            onClick={() => void deploy()}
            disabled={busy || disabled}
            data-testid="deploy-bar-deploy"
          >
            {t("admin.deploy.deployNow")}
          </Button>
        ) : null}
        {error ? (
          <p role="alert" className="text-xs text-danger" data-testid="deploy-bar-error">
            {error}
          </p>
        ) : null}
      </div>
      <DeployStatusCard kind="process" showDeploy={false} testId="deploy-bar-status" />
      {dialog}
    </div>
  );
}
