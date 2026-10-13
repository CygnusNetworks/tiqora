import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Dialog } from "@/components/ui/Dialog";
import { SoftChip } from "@/components/ui/StatusChip";
import { Spinner } from "@/components/ui/Spinner";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { toBcp47 } from "@/i18n";
import { apiErrorMessage } from "@/lib/avatarApi";
import { formatDateTime } from "@/lib/format";
import { pluginsApi, type PluginRepository } from "@/lib/pluginsApi";

const LIST_KEY = ["admin", "plugins", "repositories"] as const;

const INPUT_CLASS = "w-full rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink";

/** Add / edit dialog of an admin-entered repository. `repo = null` adds a new one. */
function RepositoryDialog({ repo, onClose }: { repo: PluginRepository | null; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [name, setName] = useState(repo?.name ?? "");
  const [url, setUrl] = useState(repo?.url ?? "");
  const [key, setKey] = useState(repo?.public_key ?? "");
  const [enabled, setEnabled] = useState(repo?.enabled ?? true);
  const saveM = useMutation({
    mutationFn: () => {
      const body = { name: name.trim(), url: url.trim(), public_key: key.trim() || null, enabled };
      return repo ? pluginsApi.updateRepository(repo.id, body) : pluginsApi.createRepository(body);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "plugins"] });
      onClose();
    },
  });
  const valid = name.trim() !== "" && url.trim() !== "";
  return (
    <Dialog
      open
      onClose={onClose}
      title={repo ? t("admin.plugins.repositories.edit") : t("admin.plugins.repositories.add")}
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            disabled={!valid || saveM.isPending}
            onClick={() => saveM.mutate()}
            data-testid="repository-save"
          >
            {t("common.save")}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm" data-testid="repository-dialog">
        <label className="block text-ink">
          <span className="mb-1 block text-xs font-medium text-muted">{t("admin.plugins.repositories.name")}</span>
          <input
            value={name}
            maxLength={200}
            onChange={(e) => setName(e.target.value)}
            className={INPUT_CLASS}
            data-testid="repository-name"
          />
        </label>
        <label className="block text-ink">
          <span className="mb-1 block text-xs font-medium text-muted">{t("admin.plugins.repositories.url")}</span>
          <input
            type="url"
            value={url}
            maxLength={1000}
            placeholder="https://"
            onChange={(e) => setUrl(e.target.value)}
            className={INPUT_CLASS}
            data-testid="repository-url"
          />
        </label>
        <label className="block text-ink">
          <span className="mb-1 block text-xs font-medium text-muted">{t("admin.plugins.repositories.key")}</span>
          <input
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className={`${INPUT_CLASS} font-mono`}
            aria-describedby="repository-key-hint"
            data-testid="repository-key"
          />
          <span id="repository-key-hint" className="mt-1 block text-xs text-muted">
            {t("admin.plugins.repositories.keyHint")}
          </span>
        </label>
        <label className="flex items-center gap-2 text-ink">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            data-testid="repository-enabled"
          />
          {t("admin.plugins.repositories.enabled")}
        </label>
        {saveM.isError && (
          <p role="alert" className="text-danger" data-testid="repository-error">
            {apiErrorMessage(saveM.error, t("admin.plugins.repositories.saveFailed"))}
          </p>
        )}
      </div>
    </Dialog>
  );
}

function RefreshProgress({ repoId, jobId, onDone }: { repoId: number; jobId: number; onDone: () => void }) {
  const { job, cancel, isCancelling } = useJob(jobId);
  const done = useRef(false);
  useEffect(() => {
    if (job && ["succeeded", "failed", "cancelled"].includes(job.status) && !done.current) {
      done.current = true;
      onDone();
    }
  }, [job, onDone]);
  if (!job) return null;
  return <JobProgress job={job} onCancel={cancel} cancelling={isCancelling} testId={`repository-progress-${repoId}`} />;
}

/** "Paketquellen": the repositories plugin lists are read from (admin-entered and from the server configuration). */
export function RepositoriesTab() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const qc = useQueryClient();
  const listQ = useQuery({
    queryKey: LIST_KEY,
    queryFn: ({ signal }) => pluginsApi.repositories(signal),
  });
  const [editing, setEditing] = useState<PluginRepository | "new" | null>(null);
  const [deleting, setDeleting] = useState<PluginRepository | null>(null);
  const [refreshing, setRefreshing] = useState<{ repoId: number; jobId: number } | null>(null);
  const reload = () => void qc.invalidateQueries({ queryKey: ["admin", "plugins"] });

  const refreshM = useMutation({
    mutationFn: (id: number) => pluginsApi.refreshRepository(id).then((a) => ({ repoId: id, jobId: a.job_id })),
    onSuccess: setRefreshing,
  });
  const deleteM = useMutation({
    mutationFn: (id: number) => pluginsApi.deleteRepository(id),
    onSuccess: () => {
      setDeleting(null);
      reload();
    },
  });

  if (listQ.isLoading) return <Spinner />;
  if (listQ.isError || !listQ.data) {
    return (
      <p role="alert" className="text-sm text-danger">
        {apiErrorMessage(listQ.error, t("admin.plugins.repositories.loadError"))}
      </p>
    );
  }
  const repos = listQ.data;
  return (
    <div className="space-y-3" data-testid="plugin-repositories">
      <div className="flex justify-end">
        <Button variant="primary" onClick={() => setEditing("new")} data-testid="repository-add">
          {t("admin.plugins.repositories.add")}
        </Button>
      </div>
      {refreshM.isError && (
        <p role="alert" className="text-sm text-danger">
          {apiErrorMessage(refreshM.error, t("admin.plugins.repositories.checkFailed"))}
        </p>
      )}
      {deleteM.isError && (
        <p role="alert" className="text-sm text-danger">
          {apiErrorMessage(deleteM.error, t("admin.plugins.repositories.deleteFailed"))}
        </p>
      )}
      {repos.length === 0 ? (
        <p className="rounded-xl border border-hairline bg-surface p-4 text-sm text-muted" data-testid="repositories-empty">
          {t("admin.plugins.catalog.noRepositories")}
        </p>
      ) : (
        <ul className="list-none space-y-3">
          {repos.map((r) => {
            const busy = refreshing?.repoId === r.id || (refreshM.isPending && refreshM.variables === r.id);
            return (
              <li key={r.id} className="rounded-xl border border-hairline bg-surface p-4" data-testid={`repository-row-${r.id}`}>
                <div className="flex flex-wrap items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <h2 className="font-display text-base font-semibold text-ink">{r.name}</h2>
                      {r.origin === "env" && (
                        <SoftChip color="var(--color-muted)">{t("admin.plugins.repositories.fromEnv")}</SoftChip>
                      )}
                      <SoftChip color={r.verified ? "var(--color-green)" : "var(--color-muted)"}>
                        {r.verified ? t("admin.plugins.repositories.verified") : t("admin.plugins.repositories.unverified")}
                      </SoftChip>
                      {!r.enabled && <SoftChip color="var(--color-muted)">{t("admin.plugins.repositories.disabled")}</SoftChip>}
                    </div>
                    <p className="mt-0.5 break-all text-xs text-muted">{r.url}</p>
                    <p className="mt-0.5 text-xs text-muted">
                      {t("admin.plugins.repositories.pluginCount", { count: r.plugin_count })}
                      {" · "}
                      {t("admin.plugins.repositories.lastCheck")}:{" "}
                      {r.last_checked_at ? formatDateTime(r.last_checked_at, locale) : t("admin.plugins.repositories.never")}
                    </p>
                    {r.last_error && (
                      <p role="alert" className="mt-1 break-words text-sm text-danger">
                        {r.last_error}
                      </p>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={busy}
                      onClick={() => refreshM.mutate(r.id)}
                      data-testid={`repository-check-${r.id}`}
                    >
                      {t("admin.plugins.repositories.check")}
                    </Button>
                    {r.origin === "admin" && (
                      <>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => setEditing(r)}
                          data-testid={`repository-edit-${r.id}`}
                        >
                          {t("admin.plugins.repositories.edit")}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setDeleting(r)}
                          data-testid={`repository-delete-${r.id}`}
                        >
                          {t("admin.plugins.repositories.delete")}
                        </Button>
                      </>
                    )}
                  </div>
                </div>
                {refreshing?.repoId === r.id && (
                  <div className="mt-3">
                    <RefreshProgress
                      repoId={r.id}
                      jobId={refreshing.jobId}
                      onDone={() => {
                        reload();
                      }}
                    />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {editing && (
        <RepositoryDialog
          key={editing === "new" ? "new" : editing.id}
          repo={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
      <ConfirmDialog
        open={deleting != null}
        title={t("admin.plugins.repositories.deleteTitle")}
        message={t("admin.plugins.repositories.deleteMessage", { name: deleting?.name ?? "" })}
        confirmLabel={t("admin.plugins.repositories.delete")}
        variant="danger"
        pending={deleteM.isPending}
        onConfirm={() => deleting && deleteM.mutate(deleting.id)}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
