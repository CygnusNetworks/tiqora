import { useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import { pluginsApi, type RestartStatus } from "@/lib/pluginsApi";

const POLL_MS = 2000;
/** Network errors are expected while the api restarts; after this long they are shown as errors. */
const GIVE_UP_MS = 5 * 60 * 1000;

export const RESTART_QUERY_KEY = ["admin", "plugins", "restart"] as const;

function isOver(status: RestartStatus): boolean {
  return status.failed_message != null || status.finished_at != null;
}

/**
 * Follows a coordinated restart. `generationHint` is the generation of the
 * latest restart that existed BEFORE this one was requested: a status that is
 * not newer than the hint is not ours yet, so the panel keeps waiting. Without
 * a hint (and without a coordinated status) the restart has to be done by hand.
 */
export function RestartPanel({ generationHint }: { generationHint?: number | null }) {
  const { t } = useTranslation();
  const startedAt = useRef(Date.now());
  const hint = generationHint ?? null;
  const isOurs = (s: RestartStatus | null | undefined): s is RestartStatus =>
    s != null && (hint == null || s.generation > hint);
  const q = useQuery({
    queryKey: RESTART_QUERY_KEY,
    queryFn: ({ signal }) => pluginsApi.restartStatus(signal),
    // Polling carries on through errors: the web interface is down while it restarts.
    refetchInterval: (query) => {
      const data = query.state.data;
      return isOurs(data) && isOver(data) ? false : POLL_MS;
    },
    retry: false,
    gcTime: 0,
  });
  const status = q.data;
  const ours = isOurs(status) ? status : null;

  if (!q.isError && (hint == null || status?.coordinated === false) && !q.isPending && (status == null || !status.coordinated)) {
    return (
      <p className="text-sm text-ink" data-testid="plugin-restart-manual">
        {t("admin.plugins.restart.manual")}
      </p>
    );
  }

  if (ours && ours.failed_message != null) {
    return (
      <p role="alert" className="text-sm text-danger" data-testid="plugin-restart-failed">
        {ours.failed_message}
      </p>
    );
  }
  if (ours && ours.finished_at != null) {
    return (
      <div className="flex flex-wrap items-center gap-3" data-testid="plugin-restart-done">
        <p className="text-sm text-ink">{t("admin.plugins.restart.done")}</p>
        <Button size="sm" variant="primary" onClick={() => window.location.reload()}>
          {t("admin.plugins.restart.reload")}
        </Button>
      </div>
    );
  }
  if (q.isError && Date.now() - startedAt.current > GIVE_UP_MS) {
    return (
      <p role="alert" className="text-sm text-danger" data-testid="plugin-restart-error">
        {apiErrorMessage(q.error, t("admin.plugins.restart.apiTimeout"))}
      </p>
    );
  }
  return (
    <div className="space-y-2" role="status" data-testid="plugin-restart-running">
      <p className="flex items-center gap-2 text-sm text-ink">
        <Spinner />
        {q.isError ? t("admin.plugins.restart.apiDown") : t("admin.plugins.restart.running")}
      </p>
      {ours && !q.isError && (
        <ul className="space-y-0.5 text-xs text-muted">
          {ours.roles.map((row) => (
            <li key={row.role} className="flex gap-2">
              <span>{t(`admin.plugins.processes.roles.${row.role}`, { defaultValue: row.role })}</span>
              <span>{t(`admin.plugins.restart.state.${row.state}`)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
