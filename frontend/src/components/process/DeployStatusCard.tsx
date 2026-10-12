import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { apiErrorMessage } from "@/lib/avatarApi";
import { deployStatusApi, type DeployKind } from "@/lib/deployStatusApi";
import { formatDateTime } from "@/lib/format";
import { Button } from "@/components/ui/Button";

export const DEPLOY_STATUS_QUERY_KEY = ["admin", "deploy-status"] as const;

/** While Znuny has not applied a deployment yet, the status is re-read this often … */
export const SYNC_POLL_MS = 5_000;
/** … for at most this long; after that the "overdue" text takes over. */
export const SYNC_GIVE_UP_MS = 5 * 60_000;

/**
 * Which revision of the process / ACL set the engine runs: "Bereitgestellt am … (aus Znuny)", the
 * number of edits not deployed yet, and the draft fallback when no revision exists. Where Tiqora
 * may deploy itself (`can_deploy_from_tiqora`) a pending state offers "Bereitstellen". Renders
 * nothing while loading or on error — it is a hint, never a blocker.
 */
export function DeployStatusCard({
  kind,
  testId,
  showDeploy = true,
  pollMs = SYNC_POLL_MS,
  giveUpMs = SYNC_GIVE_UP_MS,
}: {
  kind: DeployKind;
  testId?: string;
  /** False where a parent (the designer's DeployBar) owns the deploy button. */
  showDeploy?: boolean;
  pollMs?: number;
  giveUpMs?: number;
}) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const [gaveUp, setGaveUp] = useState(false);
  const statusQ = useQuery({
    queryKey: DEPLOY_STATUS_QUERY_KEY,
    queryFn: ({ signal }) => deployStatusApi.status(signal),
    refetchInterval: (query) =>
      query.state.data?.[kind].znuny_sync.state === "pending" && !gaveUp ? pollMs : false,
  });
  const syncState = statusQ.data?.[kind].znuny_sync.state;
  // Polling stops after `giveUpMs`; a new pending period starts the clock again.
  useEffect(() => {
    if (syncState !== "pending") {
      setGaveUp(false);
      return;
    }
    const timer = setTimeout(() => setGaveUp(true), giveUpMs);
    return () => clearTimeout(timer);
  }, [syncState, giveUpMs]);
  const deployM = useMutation({
    mutationFn: () => deployStatusApi.deploy(kind),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: DEPLOY_STATUS_QUERY_KEY }),
        qc.invalidateQueries({ queryKey: ["admin", kind === "acl" ? "acl" : "processes"] }),
        qc.invalidateQueries({ queryKey: ["admin", "process-designer"] }),
      ]);
    },
  });

  const status = statusQ.data;
  if (!status) return null;
  const kindStatus = status[kind];
  // Per kind: the ACL page must not offer "Bereitstellen" where the server would answer 409.
  const canDeploy = showDeploy && kindStatus.deploy_mode !== null && kindStatus.pending_changes > 0;
  const sync = kindStatus.znuny_sync;
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
      {sync.state === "pending" && (
        <p
          className={gaveUp || sync.overdue ? "text-amber" : "text-muted"}
          data-testid={gaveUp || sync.overdue ? "deploy-sync-overdue" : "deploy-sync-pending"}
        >
          {t(gaveUp || sync.overdue ? "admin.deploy.sync.overdue" : "admin.deploy.sync.pending")}
        </p>
      )}
      {sync.state === "ok" && (
        <p className="text-green" data-testid="deploy-sync-ok">
          {t("admin.deploy.sync.ok")}
        </p>
      )}
      {sync.state === "addon_missing" && (
        <p className="text-amber" data-testid="deploy-sync-addon-missing">
          {t("admin.deploy.sync.addon_missing")}
        </p>
      )}
      {sync.state === "error" && (
        <div
          role="alert"
          className="rounded-md border border-danger bg-danger/10 px-3 py-2 text-danger"
          data-testid="deploy-sync-error"
        >
          <p className="font-medium">{t("admin.deploy.sync.error")}</p>
          {sync.message && <p className="text-xs">{sync.message}</p>}
          <p className="text-xs">{t("admin.deploy.sync.errorHint")}</p>
        </div>
      )}
      {status.ownership_active && (
        <p className="text-xs text-muted" data-testid="deploy-status-ownership">
          {t("admin.deploy.ownership")}
        </p>
      )}
      {deployM.isError && (
        <p className="text-xs text-danger" role="alert" data-testid="deploy-status-error">
          {apiErrorMessage(deployM.error, t("admin.deploy.deployFailed"))}
        </p>
      )}
    </div>
  );
}
