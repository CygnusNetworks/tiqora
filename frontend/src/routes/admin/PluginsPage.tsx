import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Button } from "@/components/ui/Button";
import { SoftChip } from "@/components/ui/StatusChip";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { toBcp47 } from "@/i18n";
import { apiErrorMessage } from "@/lib/avatarApi";
import { formatDateTime } from "@/lib/format";
import { pluginsApi, type PluginAdmin } from "@/lib/pluginsApi";
import { isPluginSafeMode } from "@/plugins/safeMode";

const QUERY_KEY = ["admin", "plugins"] as const;

type Status = PluginAdmin["status"];

/** Chip colour per status (R-SOFTCHIP): green = running, danger = needs attention, muted = nothing to do. */
const STATUS_COLOR: Record<Status, string> = {
  active: "var(--color-green)",
  restart_required: "var(--color-amber)",
  unknown: "var(--color-muted)",
  disabled: "var(--color-muted)",
  failed: "var(--color-danger)",
  quarantined: "var(--color-danger)",
  invalid: "var(--color-danger)",
  incompatible: "var(--color-danger)",
};

const POINT_KEYS: Record<string, string> = {
  "core.events.subscribers": "events",
  "core.http.routes": "routes",
  "core.http.public_routes": "public_routes",
  "core.mcp.tools": "mcp_tools",
  "core.worker.scheduled_tasks": "scheduled_tasks",
  "core.reporting.builtin_reports": "builtin_reports",
  "core.reporting.sources": "report_sources",
};

const SLOT_KEYS: Record<string, string> = {
  "agent.nav": "nav",
  "agent.route": "route",
  "ticket.sidebar": "ticket_sidebar",
  "dashboard.widget": "widget",
};

function capabilityText(t: TFunction, cap: PluginAdmin["capabilities"][number]): string {
  if (cap.kind === "outbound_host") return t("admin.plugins.capabilities.host", { host: cap.value });
  if (cap.kind === "extension_point") {
    const key = POINT_KEYS[cap.value];
    return key
      ? t(`admin.plugins.capabilities.point.${key}`)
      : t("admin.plugins.capabilities.point.other", { value: cap.value });
  }
  const key = SLOT_KEYS[cap.value];
  return key
    ? t(`admin.plugins.capabilities.slot.${key}`)
    : t("admin.plugins.capabilities.slot.other", { value: cap.value });
}

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
                  <span className="text-xs text-muted">
                    {hook.kind === "event" ? t("admin.plugins.hooks.onTicketChange") : (hook.schedule ?? "")}
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

function PluginCard({
  plugin,
  disabled,
  onToggle,
  onRetry,
}: {
  plugin: PluginAdmin;
  disabled: boolean;
  onToggle: (enabled: boolean) => void;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const id = plugin.id;
  return (
    <li
      className="rounded-xl border border-hairline bg-surface p-4"
      data-testid={`plugin-card-${id}`}
    >
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h2 className="font-display text-base font-semibold text-ink">{plugin.name}</h2>
            <SoftChip color={STATUS_COLOR[plugin.status]} data-testid={`plugin-status-${id}`}>
              {t(`admin.plugins.status.${plugin.status}`)}
            </SoftChip>
          </div>
          <p className="mt-0.5 text-xs text-muted">
            {t("admin.plugins.version", { version: plugin.version })} · {t("admin.plugins.publisher", { publisher: plugin.publisher })}
          </p>
          {plugin.description && <p className="mt-1.5 text-sm text-ink">{plugin.description}</p>}
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
  const busy = toggleM.isPending || retryM.isPending;
  const actionError = toggleM.error ?? retryM.error;

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

  const { plugins, safe_mode: safeMode } = listQ.data;
  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4" data-testid="admin-plugins-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.nav.plugins")}</h1>
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
      {actionError && (
        <p role="alert" className="text-sm text-danger" data-testid="admin-plugins-action-error">
          {apiErrorMessage(actionError, t("admin.plugins.actionError"))}
        </p>
      )}

      {plugins.length === 0 ? (
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
              onToggle={(enabled) => toggleM.mutate({ id: plugin.id, enabled })}
              onRetry={() => retryM.mutate(plugin.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
