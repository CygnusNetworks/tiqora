import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { ApiError } from "@/lib/api";
import { deployStatusApi, type DeployKind } from "@/lib/deployStatusApi";
import { formatDateTime } from "@/lib/format";
import { Button } from "@/components/ui/Button";

export const DEPLOY_STATUS_QUERY_KEY = ["admin", "deploy-status"] as const;

/**
 * Which revision of the process / ACL set the engine runs: "Bereitgestellt am … (aus Znuny)", the
 * number of edits not deployed yet, and the draft fallback when no revision exists. Where Tiqora
 * may deploy itself (`can_deploy_from_tiqora`) a pending state offers "Bereitstellen". Renders
 * nothing while loading or on error — it is a hint, never a blocker.
 */
export function DeployStatusCard({ kind, testId }: { kind: DeployKind; testId?: string }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const statusQ = useQuery({
    queryKey: DEPLOY_STATUS_QUERY_KEY,
    queryFn: ({ signal }) => deployStatusApi.status(signal),
  });
  const deployM = useMutation({
    mutationFn: () => deployStatusApi.deploy(kind),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: DEPLOY_STATUS_QUERY_KEY }),
        qc.invalidateQueries({ queryKey: ["admin", kind === "acl" ? "acl" : "processes"] }),
      ]);
    },
  });

  const status = statusQ.data;
  if (!status) return null;
  const kindStatus = status[kind];
  const canDeploy = status.can_deploy_from_tiqora && kindStatus.pending_changes > 0;
  const rev = kindStatus.revision;

  return (
    <div
      className="space-y-1.5 rounded-lg border border-hairline bg-surface p-3 text-sm"
      data-testid={testId ?? `deploy-status-${kind}`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">
          {t("admin.deploy.title")}
        </span>
        {rev && (
          <span className="text-ink" data-testid="deploy-status-revision">
            {t("admin.deploy.deployedAt", {
              date: formatDateTime(rev.deployed_at, toBcp47(i18n.language)),
            })}{" "}
            <span className="text-muted">
              ({t(`admin.deploy.source.${rev.source}`, { defaultValue: rev.source })})
            </span>
          </span>
        )}
      </div>
      {kindStatus.running_live && (
        <p className="text-danger" data-testid="deploy-status-running-live">
          {t("admin.deploy.runningLive")}
        </p>
      )}
      {kindStatus.pending_changes > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-amber" data-testid="deploy-status-pending">
            {t(status.can_deploy_from_tiqora ? "admin.deploy.pendingHere" : "admin.deploy.pending", {
              count: kindStatus.pending_changes,
            })}
          </p>
          {canDeploy && (
            <Button
              size="sm"
              variant="primary"
              disabled={deployM.isPending}
              onClick={() => deployM.mutate()}
              data-testid="deploy-status-deploy"
            >
              {t("admin.deploy.deployNow")}
            </Button>
          )}
        </div>
      )}
      {status.ownership_active && (
        <p className="text-xs text-muted" data-testid="deploy-status-ownership">
          {t("admin.deploy.ownership")}
        </p>
      )}
      {deployM.isError && (
        <p className="text-xs text-danger" role="alert" data-testid="deploy-status-error">
          {deployM.error instanceof ApiError ? deployM.error.message : t("admin.deploy.deployFailed")}
        </p>
      )}
    </div>
  );
}
