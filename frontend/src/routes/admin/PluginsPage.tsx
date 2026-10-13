import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { SoftChip } from "@/components/ui/StatusChip";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { Tabs } from "@/components/ui/Tabs";
import { useJob } from "@/components/jobs/useJob";
import { toBcp47 } from "@/i18n";
import { apiErrorMessage } from "@/lib/avatarApi";
import { formatDateTime } from "@/lib/format";
import { pluginPointsApi } from "@/lib/pluginPointsApi";
import { pluginsApi, type PluginAdmin, type PluginPackage } from "@/lib/pluginsApi";
import { isPluginSafeMode } from "@/plugins/safeMode";
import { capabilityText } from "./plugins/capabilities";
import { InstallDialog } from "./plugins/InstallDialog";
import { RestartPanel } from "./plugins/RestartPanel";
import { sourceLabel, trustColor, trustLabelKey } from "./plugins/trust";
import { PLUGIN_TABS, parsePluginTab } from "./plugins/tabs";
import { UninstallDialog } from "./plugins/UninstallDialog";

const QUERY_KEY = ["admin", "plugins"] as const;

type Status = PluginAdmin["status"];

/** Chip colour per status (R-SOFTCHIP): green = running, danger = needs attention, muted = nothing to do. */
const STATUS_COLOR: Record<Status, string> = {
  active: "var(--color-green)",
  restart_required: "var(--color-muted)",
  unknown: "var(--color-muted)",
  disabled: "var(--color-muted)",
  failed: "var(--color-danger)",
  quarantined: "var(--color-danger)",
  invalid: "var(--color-danger)",
  incompatible: "var(--color-danger)",
};

const NUMBER_GENERATORS_POINT = "core.tickets.number_generators";
const NUMBER_GENERATORS_KEY = ["admin", "plugins", "number-generators"] as const;

function needsRetry(plugin: PluginAdmin): boolean {
  return (
    plugin.status === "quarantined" ||
    plugin.status === "failed" ||
    plugin.hooks.some((hook) => hook.tripped)
  );
}

function PluginDetails({ plugin }: { plugin: PluginAdmin }) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const roleLabel = (role: string) => t(`admin.plugins.processes.roles.${role}`, { defaultValue: role });
  const stateLabel = (state: string) =>
    t(`admin.plugins.processes.states.${state}`, { defaultValue: state });
  return (
    <div className="mt-3 space-y-4 border-t border-hairline pt-3 text-sm" data-testid={`plugin-details-panel-${plugin.id}`}>
      {plugin.runtime.length > 0 && (
        <section>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
            {t("admin.plugins.processes.title")}
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="text-xs text-muted">
                <tr>
                  <th scope="col" className="py-1 pr-3 font-medium">{t("admin.plugins.processes.process")}</th>
                  <th scope="col" className="py-1 pr-3 font-medium">{t("admin.plugins.processes.state")}</th>
                  <th scope="col" className="py-1 pr-3 font-medium">{t("admin.plugins.processes.version")}</th>
                  <th scope="col" className="py-1 font-medium">{t("admin.plugins.processes.error")}</th>
                </tr>
              </thead>
              <tbody>
                {plugin.runtime.map((row) => (
                  <tr key={row.role} className="border-t border-hairline align-top">
                    <td className="py-1 pr-3 text-ink">{roleLabel(row.role)}</td>
                    <td className="py-1 pr-3 text-ink">{stateLabel(row.state)}</td>
                    <td className="py-1 pr-3 text-muted">{row.version ?? "—"}</td>
                    <td className="py-1 break-words text-danger">{row.error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {plugin.hooks.length > 0 && (
        <section>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
            {t("admin.plugins.hooks.title")}
          </h3>
          <ul className="space-y-2">
            {plugin.hooks.map((hook) => (
              <li key={hook.key} className="rounded-lg bg-surface-subtle px-3 py-2" data-testid={`plugin-hook-${plugin.id}-${hook.key}`}>
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium text-ink">{hook.name}</span>
                  {hook.kind === "sync" && (
                    <span className="text-xs text-muted">{t("admin.plugins.hooks.kind.sync")}</span>
                  )}
                  <span className="text-xs text-muted">
                    {hook.kind === "event"
                      ? t("admin.plugins.hooks.onTicketChange")
                      : hook.kind === "sync"
                        ? t(`admin.plugins.hooks.operation.${hook.schedule ?? ""}`, { defaultValue: hook.schedule ?? "" })
                        : (hook.schedule ?? "")}
                  </span>
                </div>
                {hook.tripped && (
                  <p className="mt-1 text-xs font-semibold text-danger">
                    {t("admin.plugins.hooks.stopped", { count: hook.consecutive_failures })}
                  </p>
                )}
                {hook.last_error && (
                  <p className="mt-1 break-words text-xs text-muted">
                    <span className="sr-only">{t("admin.plugins.hooks.lastError")}: </span>
                    <span className="text-danger">{hook.last_error}</span>
                    {hook.last_error_at && <span> ({formatDateTime(hook.last_error_at, locale)})</span>}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {plugin.migration_error && (
        <section data-testid={`plugin-migration-error-${plugin.id}`}>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
            {t("admin.plugins.migrationError")}
          </h3>
          <p className="break-words text-danger">{plugin.migration_error}</p>
        </section>
      )}

      <section>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
          {t("admin.plugins.capabilities.title")}
        </h3>
        {plugin.capabilities.length === 0 ? (
          <p className="text-muted">{t("admin.plugins.capabilities.none")}</p>
        ) : (
          <ul className="list-disc space-y-0.5 pl-5 text-ink">
            {plugin.capabilities.map((cap) => (
              <li key={`${cap.kind}:${cap.value}`}>{capabilityText(t, cap)}</li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** "Ticketnummer-Schema" of a plugin that offers number generators: pick one for new tickets, or go back to Znuny's. */
function NumberGeneratorBox({ plugin }: { plugin: PluginAdmin }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: NUMBER_GENERATORS_KEY,
    queryFn: ({ signal }) => pluginPointsApi.numberGenerators(signal),
  });
  const setM = useMutation({
    mutationFn: (key: string | null) => pluginPointsApi.setNumberGenerator(key),
    onSuccess: (data) => qc.setQueryData(NUMBER_GENERATORS_KEY, data),
  });
  if (q.isError) {
    // Not "nothing offered": the plugin registers generators, the list just failed to load.
    return (
      <p role="alert" className="mt-3 text-xs text-danger" data-testid={`plugin-number-generator-error-${plugin.id}`}>
        {t("admin.plugins.numberGenerator.loadError")}
      </p>
    );
  }
  const own = (q.data?.available ?? []).filter((g) => g.plugin_id === plugin.id);
  if (!q.data || own.length === 0) return null;
  const activeOwn = own.some((g) => g.key === q.data.active);
  return (
    <section
      className="mt-3 rounded-lg bg-surface-subtle px-3 py-2 text-sm"
      data-testid={`plugin-number-generator-${plugin.id}`}
    >
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
        {t("admin.plugins.numberGenerator.title")}
      </h3>
      <ul className="mt-1 space-y-1.5">
        {own.map((g) => (
          <li key={g.key} className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-medium text-ink">{g.label}</span>
            {g.example && (
              <span className="text-xs text-muted">{t("admin.plugins.numberGenerator.example", { example: g.example })}</span>
            )}
            {g.key === q.data.active ? (
              <SoftChip color="var(--color-green)">{t("admin.plugins.numberGenerator.active")}</SoftChip>
            ) : (
              <Button
                size="sm"
                variant="secondary"
                disabled={setM.isPending}
                onClick={() => setM.mutate(g.key)}
              >
                {t("admin.plugins.numberGenerator.use")}
              </Button>
            )}
          </li>
        ))}
      </ul>
      {activeOwn && (
        <Button
          size="sm"
          variant="ghost"
          className="mt-1.5"
          disabled={setM.isPending}
          onClick={() => setM.mutate(null)}
        >
          {t("admin.plugins.numberGenerator.reset")}
        </Button>
      )}
      <p className="mt-1.5 text-xs text-muted">
        {t("admin.plugins.numberGenerator.znunyNote", { generator: q.data.znuny_generator })}
      </p>
      {setM.isError && (
        <p role="alert" className="mt-1 text-xs text-danger">
          {apiErrorMessage(setM.error, t("admin.plugins.actionError"))}
        </p>
      )}
    </section>
  );
}

type FocusTarget = { id: string; what: "switch" | "retry" };

function PluginCard({
  plugin,
  disabled,
  focusWhat,
  onFocused,
  onToggle,
  onRetry,
  repositoryNames,
  updateBusy,
  onUpdate,
  onUninstall,
}: {
  plugin: PluginAdmin;
  disabled: boolean;
  /** Set after an action on this card: where focus goes once the card is enabled again. */
  focusWhat: FocusTarget["what"] | null;
  onFocused: () => void;
  onToggle: (enabled: boolean) => void;
  onRetry: () => void;
  repositoryNames: ReadonlyMap<number, string>;
  updateBusy: boolean;
  onUpdate: () => void;
  onUninstall: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const id = plugin.id;
  const rowRef = useRef<HTMLLIElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  // A disabled control drops focus; give it back (or, when the "Erneut
  // versuchen" button is gone, move it to the plugin's heading).
  useEffect(() => {
    if (!focusWhat || disabled) return;
    const row = rowRef.current;
    if (focusWhat === "switch") {
      row?.querySelector<HTMLElement>(`[data-testid="plugin-enabled-${id}"]`)?.focus();
    } else {
      const retry = row?.querySelector<HTMLElement>(`[data-testid="plugin-retry-${id}"]`);
      (retry ?? headingRef.current)?.focus();
    }
    onFocused();
  }, [focusWhat, disabled, id, onFocused]);
  return (
    <li
      ref={rowRef}
      className="rounded-xl border border-hairline bg-surface p-4"
      data-testid={`plugin-card-${id}`}
    >
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h2
              ref={headingRef}
              tabIndex={-1}
              className="font-display text-base font-semibold text-ink focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              {plugin.name}
            </h2>
            <SoftChip color={STATUS_COLOR[plugin.status]} data-testid={`plugin-status-${id}`}>
              {t(`admin.plugins.status.${plugin.status}`)}
            </SoftChip>
          </div>
          <p className="mt-0.5 text-xs text-muted">
            {t("admin.plugins.version", { version: plugin.version })} · {t("admin.plugins.publisher", { publisher: plugin.publisher })}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <SoftChip color="var(--color-muted)" data-testid={`plugin-source-${id}`}>
              {sourceLabel(t, plugin, repositoryNames)}
            </SoftChip>
            {plugin.signature_status && (
              <SoftChip color={trustColor(plugin.signature_status)} data-testid={`plugin-trust-${id}`}>
                {t(trustLabelKey(plugin.signature_status))}
              </SoftChip>
            )}
          </div>
          {plugin.description && <p className="mt-1.5 text-sm text-ink">{plugin.description}</p>}
          {plugin.update && (
            <div className="mt-1.5 text-sm" data-testid={`plugin-update-${id}`}>
              <span className="font-medium text-ink">
                {t("admin.plugins.update.available", { version: plugin.update.version })}
              </span>
              {plugin.update.image_plugin && (
                <p className="mt-0.5 text-xs text-muted" data-testid={`plugin-update-image-hint-${id}`}>
                  {t("admin.plugins.update.imageHint")}
                </p>
              )}
            </div>
          )}
          {plugin.status_message && (
            <p
              className={
                plugin.status === "active" || plugin.status === "disabled" || plugin.status === "unknown" || plugin.status === "restart_required"
                  ? "mt-1.5 text-sm text-muted"
                  : "mt-1.5 text-sm text-danger"
              }
              data-testid={`plugin-message-${id}`}
            >
              {plugin.status_message}
            </p>
          )}
        </div>
        <Switch
          checked={plugin.enabled}
          disabled={disabled}
          onChange={onToggle}
          aria-label={plugin.name}
          testId={`plugin-enabled-${id}`}
        />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {plugin.settings_section && (
          <Link
            to="/admin/settings/$section"
            params={{ section: plugin.settings_section }}
            data-testid={`plugin-settings-${id}`}
            className="inline-flex items-center rounded-lg border border-hairline px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          >
            {t("admin.plugins.settings")}
          </Link>
        )}
        {needsRetry(plugin) && (
          <Button size="sm" variant="secondary" disabled={disabled} onClick={onRetry} data-testid={`plugin-retry-${id}`}>
            {t("admin.plugins.retry")}
          </Button>
        )}
        {plugin.update && !plugin.update.image_plugin && (
          <Button
            size="sm"
            variant="primary"
            disabled={disabled || updateBusy}
            onClick={onUpdate}
            data-testid={`plugin-update-button-${id}`}
          >
            {t("admin.plugins.update.action")}
          </Button>
        )}
        {plugin.can_uninstall && (
          <Button
            size="sm"
            variant="secondary"
            disabled={disabled}
            onClick={onUninstall}
            data-testid={`plugin-uninstall-${id}`}
          >
            {t("admin.plugins.uninstall.button")}
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          data-testid={`plugin-details-${id}`}
        >
          {open ? t("admin.plugins.hideDetails") : t("admin.plugins.details")}
        </Button>
      </div>
      {plugin.capabilities.some((c) => c.kind === "extension_point" && c.value === NUMBER_GENERATORS_POINT) && (
        <NumberGeneratorBox plugin={plugin} />
      )}
      {open && <PluginDetails plugin={plugin} />}
    </li>
  );
}

export function PluginsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const listQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => pluginsApi.adminList(signal),
  });

  const replace = (updated: PluginAdmin) => {
    qc.setQueryData(QUERY_KEY, (prev: Awaited<ReturnType<typeof pluginsApi.adminList>> | undefined) =>
      prev ? { ...prev, plugins: prev.plugins.map((p) => (p.id === updated.id ? updated : p)) } : prev,
    );
    void qc.invalidateQueries({ queryKey: ["plugins", "frontend"] });
  };
  const toggleM = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => pluginsApi.setEnabled(id, enabled),
    onSuccess: replace,
  });
  const retryM = useMutation({
    mutationFn: (id: string) => pluginsApi.reset(id),
    onSuccess: replace,
  });
  const [focusTarget, setFocusTarget] = useState<FocusTarget | null>(null);
  const clearFocus = useCallback(() => setFocusTarget(null), []);
  const run = (target: FocusTarget, start: () => void) => {
    // A new action clears the previous error, including a stale download error.
    toggleM.reset();
    retryM.reset();
    downloadM.reset();
    setDownloadError(null);
    setDownloadMessage(null);
    setFocusTarget(target);
    start();
  };
  const fileRef = useRef<HTMLInputElement>(null);
  const [pkg, setPkg] = useState<PluginPackage | null>(null);
  const uploadM = useMutation({
    mutationFn: (file: File) => pluginsApi.uploadPackage(file),
    onSuccess: setPkg,
  });
  const onFile = (file: File | undefined) => {
    if (fileRef.current) fileRef.current.value = "";
    if (file) uploadM.mutate(file);
  };
  const [restartHint, setRestartHint] = useState<number | null>(null);
  const restartM = useMutation({
    mutationFn: async () => {
      let before = 0;
      try {
        before = (await pluginsApi.restartStatus())?.generation ?? 0;
      } catch {
        before = 0;
      }
      await pluginsApi.requestRestart();
      return before;
    },
    onSuccess: setRestartHint,
  });
  const [uninstalling, setUninstalling] = useState<PluginAdmin | null>(null);

  // Update = download the package from the repository (a job), then show it in the install dialog.
  const [downloadJobId, setDownloadJobId] = useState<number | null>(null);
  const [downloadError, setDownloadError] = useState<unknown>(null);
  // The failed job's German, admin-facing reason (signature, hash, F19 URL rejection ...).
  const [downloadMessage, setDownloadMessage] = useState<string | null>(null);
  const downloadM = useMutation({
    mutationFn: ({ repoId, pluginId, version }: { repoId: number; pluginId: string; version: string }) =>
      pluginsApi.download(repoId, { plugin_id: pluginId, version }),
    onSuccess: (accepted) => setDownloadJobId(accepted.job_id),
  });
  const download = useJob(downloadJobId);
  const handledJob = useRef<number | null>(null);
  useEffect(() => {
    const job = download.job;
    if (downloadJobId == null || !job || handledJob.current === downloadJobId) return;
    if (job.status === "succeeded") {
      handledJob.current = downloadJobId;
      const sha = job.result?.package_sha256;
      if (typeof sha !== "string") {
        setDownloadError(new Error("no package"));
        setDownloadJobId(null);
        return;
      }
      pluginsApi
        .getPackage(sha)
        .then(setPkg)
        .catch(setDownloadError)
        .finally(() => setDownloadJobId(null));
    } else if (job.status === "failed" || job.status === "cancelled") {
      handledJob.current = downloadJobId;
      setDownloadError(new Error(job.error ?? ""));
      setDownloadMessage(job.error?.trim() || null);
      setDownloadJobId(null);
    }
  }, [download.job, downloadJobId]);
  const downloading = downloadM.isPending || downloadJobId != null;
  const startUpdate = (plugin: PluginAdmin) => {
    if (!plugin.update) return;
    setDownloadError(null);
    setDownloadMessage(null);
    downloadM.mutate({ repoId: plugin.update.repository_id, pluginId: plugin.id, version: plugin.update.version });
  };

  const search = useSearch({ strict: false }) as { tab?: unknown };
  const navigate = useNavigate({ from: "/admin/plugins" });
  const tab = parsePluginTab(search.tab);
  const setTab = (next: string) => {
    const value = parsePluginTab(next);
    void navigate({ to: ".", search: { tab: value === "installed" ? undefined : value }, replace: true });
  };

  const busy = toggleM.isPending || retryM.isPending;
  const actionError = toggleM.error ?? retryM.error ?? restartM.error ?? downloadM.error ?? downloadError;

  if (listQ.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4" data-testid="admin-plugins-page">
        <Spinner />
      </div>
    );
  }
  if (listQ.isError || !listQ.data) {
    return (
      <div className="p-4 text-sm text-danger" data-testid="admin-plugins-page">
        {apiErrorMessage(listQ.error, t("admin.plugins.loadError"))}
      </div>
    );
  }

  const { plugins, safe_mode: safeMode, runtime_install: runtimeInstall } = listQ.data;
  const coordinated = listQ.data.coordinated_restart;
  const repositoryNames = new Map<number, string>();
  for (const p of plugins) if (p.update) repositoryNames.set(p.update.repository_id, p.update.repository_name);
  const restartPending = plugins.some((p) => p.status === "restart_required");
  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4" data-testid="admin-plugins-page">
      <div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="font-display text-xl font-semibold text-ink">{t("admin.nav.plugins")}</h1>
          {runtimeInstall && (
            <>
              <input
                ref={fileRef}
                type="file"
                accept=".tqplugin"
                className="hidden"
                data-testid="plugin-upload-input"
                aria-label={t("admin.plugins.upload")}
                onChange={(e) => onFile(e.target.files?.[0])}
              />
              <Button
                variant="primary"
                disabled={uploadM.isPending}
                onClick={() => fileRef.current?.click()}
                title={t("admin.plugins.uploadHint")}
                data-testid="plugin-upload"
              >
                {t("admin.plugins.upload")}
              </Button>
            </>
          )}
        </div>
        {!runtimeInstall && (
          <p className="mt-1 text-sm text-muted" data-testid="plugin-runtime-install-off">
            {t("admin.plugins.runtimeInstallOff", { reason: listQ.data.runtime_install_reason ?? "" })}
          </p>
        )}
        <p className="mt-1 text-sm text-muted">{t("admin.plugins.description")}</p>
        <p
          className="mt-2 rounded-lg border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink"
          data-testid="admin-plugins-trust-note"
        >
          {t("admin.plugins.trustNote")}
        </p>
      </div>

      {safeMode && (
        <div
          role="status"
          className="rounded-lg border border-amber/40 bg-amber/10 px-3 py-2 text-sm text-ink"
          data-testid="admin-plugins-safe-mode"
        >
          {t("admin.plugins.safeMode")}
        </div>
      )}
      {isPluginSafeMode() && (
        <div
          role="status"
          className="rounded-lg border border-amber/40 bg-amber/10 px-3 py-2 text-sm text-ink"
          data-testid="admin-plugins-browser-off"
        >
          {t("admin.plugins.browserOff")}
        </div>
      )}
      {uploadM.isError && (
        <p role="alert" className="text-sm text-danger" data-testid="plugin-upload-error">
          {apiErrorMessage(uploadM.error, t("admin.plugins.uploadFailed"))}
        </p>
      )}
      {restartPending && (
        <div className="space-y-2 rounded-lg border border-hairline bg-surface p-3" data-testid="plugin-restart-box">
          {restartHint != null ? (
            <RestartPanel generationHint={restartHint} />
          ) : coordinated ? (
            <Button variant="secondary" disabled={restartM.isPending} onClick={() => restartM.mutate()} data-testid="plugin-restart-now">
              {t("admin.plugins.restart.now")}
            </Button>
          ) : (
            <p className="text-sm text-ink">{t("admin.plugins.restart.manual")}</p>
          )}
        </div>
      )}
      {actionError != null && (
        <p role="alert" className="text-sm text-danger" data-testid="admin-plugins-action-error">
          {actionError === downloadError && downloadMessage
            ? downloadMessage
            : apiErrorMessage(
                actionError,
                actionError === downloadError || actionError === downloadM.error
                  ? t("admin.plugins.update.failed")
                  : t("admin.plugins.actionError"),
              )}
        </p>
      )}

      <Tabs
        items={PLUGIN_TABS.map((id) => ({ id, label: t(`admin.plugins.tabs.${id}`) }))}
        value={tab}
        onChange={setTab}
      />
      {downloading && (
        <p className="flex items-center gap-2 text-sm text-muted" role="status" data-testid="plugin-update-running">
          <Spinner /> {t("admin.plugins.update.running")}
        </p>
      )}

      {tab !== "installed" ? null : plugins.length === 0 ? (
        <p className="rounded-xl border border-hairline bg-surface p-4 text-sm text-muted" data-testid="admin-plugins-empty">
          {t("admin.plugins.empty")}
        </p>
      ) : (
        <ul className="list-none space-y-3">
          {plugins.map((plugin) => (
            <PluginCard
              key={plugin.id}
              plugin={plugin}
              disabled={busy}
              focusWhat={focusTarget?.id === plugin.id ? focusTarget.what : null}
              onFocused={clearFocus}
              onToggle={(enabled) =>
                run({ id: plugin.id, what: "switch" }, () => toggleM.mutate({ id: plugin.id, enabled }))
              }
              onRetry={() => run({ id: plugin.id, what: "retry" }, () => retryM.mutate(plugin.id))}
              repositoryNames={repositoryNames}
              updateBusy={downloading}
              onUpdate={() => startUpdate(plugin)}
              onUninstall={() => setUninstalling(plugin)}
            />
          ))}
        </ul>
      )}
      {uninstalling && (
        <UninstallDialog
          key={uninstalling.id}
          plugin={uninstalling}
          open
          onClose={() => setUninstalling(null)}
          coordinatedRestart={coordinated}
        />
      )}
      {pkg && (
        <InstallDialog
          key={pkg.sha256}
          pkg={pkg}
          open
          onClose={() => setPkg(null)}
          coordinatedRestart={coordinated}
        />
      )}
    </div>
  );
}
