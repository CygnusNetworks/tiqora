import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type DaemonServiceOut, type DaemonUpdate } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { formatDateTime } from "@/lib/format";
import { statusColor, type StatusColor } from "@/lib/daemonStatus";

const QUERY_KEY = ["admin", "daemons"] as const;
const REFETCH_INTERVAL_MS = 10_000;

//: postmaster/escalation/notifications/generic_agent are Znuny daemon
// takeovers and must stay mutually exclusive with the corresponding Znuny
// scheduler task — see docs/parallel-operation.md.
const TAKEOVER_SLUGS = new Set(["postmaster", "escalation", "notifications", "generic_agent"]);

const DOT_CLASS: Record<StatusColor, string> = {
  green: "bg-accent",
  amber: "bg-amber",
  red: "bg-danger",
  grey: "bg-muted",
};

export function DaemonsPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const qc = useQueryClient();
  // Uncommitted interval edits, keyed by slug, so the 10s refetch never
  // clobbers text the operator is mid-typing.
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const daemonsQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => api.getDaemons(signal),
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const updateM = useMutation({
    mutationFn: ({ slug, body }: { slug: string; body: DaemonUpdate }) =>
      api.putDaemon(slug, body),
    onSuccess: (updated) => {
      qc.setQueryData(QUERY_KEY, (prev: { services: DaemonServiceOut[] } | undefined) =>
        prev
          ? { services: prev.services.map((s) => (s.slug === updated.slug ? updated : s)) }
          : prev,
      );
      setDrafts((d) => {
        const next = { ...d };
        delete next[updated.slug];
        return next;
      });
    },
  });

  const toggle = (svc: DaemonServiceOut) => {
    updateM.mutate({ slug: svc.slug, body: { enabled: !svc.enabled } });
  };

  const commitInterval = (svc: DaemonServiceOut) => {
    const raw = drafts[svc.slug];
    if (raw === undefined) return;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed) || parsed === svc.interval_seconds) {
      setDrafts((d) => {
        const next = { ...d };
        delete next[svc.slug];
        return next;
      });
      return;
    }
    updateM.mutate({ slug: svc.slug, body: { interval_seconds: Math.max(0, Math.round(parsed)) } });
  };

  const resetInterval = (svc: DaemonServiceOut) => {
    updateM.mutate({ slug: svc.slug, body: { interval_seconds: 0 } });
  };

  if (daemonsQ.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4" data-testid="admin-daemons-page">
        <Spinner />
      </div>
    );
  }

  if (daemonsQ.isError) {
    return (
      <div className="p-4 text-sm text-danger" data-testid="admin-daemons-page">
        {t("admin.daemons.loadError")}
      </div>
    );
  }

  const services = daemonsQ.data?.services ?? [];
  const nowMs = Date.now();

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="admin-daemons-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.daemons.title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("admin.daemons.description")}</p>
      </div>

      <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-hairline text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className="px-3 py-2">{t("admin.daemons.columns.status")}</th>
              <th className="px-3 py-2">{t("admin.daemons.columns.service")}</th>
              <th className="px-3 py-2">{t("admin.daemons.columns.enabled")}</th>
              <th className="px-3 py-2">{t("admin.daemons.columns.schedule")}</th>
              <th className="px-3 py-2">{t("admin.daemons.columns.lastOk")}</th>
              <th className="px-3 py-2">{t("admin.daemons.columns.lastResult")}</th>
            </tr>
          </thead>
          <tbody>
            {services.map((svc) => {
              const color = statusColor(svc, nowMs);
              const draft = drafts[svc.slug];
              const intervalValue = draft ?? String(svc.interval_seconds ?? "");
              return (
                <tr
                  key={svc.slug}
                  className="border-b border-hairline last:border-0"
                  data-testid={`daemon-row-${svc.slug}`}
                >
                  <td className="px-3 py-2 align-top">
                    <span
                      className="inline-flex items-center gap-1.5"
                      data-testid={`daemon-status-${svc.slug}`}
                      data-status={color}
                      title={svc.last_error ?? undefined}
                    >
                      <span className={`h-2.5 w-2.5 rounded-full ${DOT_CLASS[color]}`} />
                      {t(`admin.daemons.status.${color}`)}
                    </span>
                    {svc.last_error ? (
                      <p className="mt-1 max-w-xs truncate text-xs text-danger">{svc.last_error}</p>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 align-top">
                    <div className="font-medium text-ink">
                      {t(`admin.daemons.services.${svc.slug}.name`)}
                    </div>
                    <div className="text-xs text-muted">
                      {t(`admin.daemons.services.${svc.slug}.description`)}
                    </div>
                    {TAKEOVER_SLUGS.has(svc.slug) ? (
                      <p className="mt-1 text-xs text-amber">{t("admin.daemons.takeoverNote")}</p>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 align-top">
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        data-testid={`daemon-toggle-${svc.slug}`}
                        checked={svc.enabled}
                        disabled={!svc.toggleable || updateM.isPending}
                        onChange={() => toggle(svc)}
                        className="rounded border-hairline"
                      />
                      {!svc.toggleable ? (
                        <span className="rounded bg-surface-subtle px-1.5 py-0.5 text-[11px] font-medium text-muted">
                          {t("admin.daemons.alwaysOn")}
                        </span>
                      ) : null}
                    </label>
                  </td>
                  <td className="px-3 py-2 align-top">
                    {svc.schedule === "daily" ? (
                      <span className="text-ink">
                        {t("admin.daemons.dailyAt", { time: svc.daily_at })}
                      </span>
                    ) : svc.slug === "poller" ? (
                      <span className="text-ink">
                        {t("admin.daemons.intervalSeconds", { seconds: svc.interval_seconds })}
                      </span>
                    ) : (
                      <div className="flex items-center gap-1.5">
                        <input
                          type="number"
                          min={5}
                          data-testid={`daemon-interval-${svc.slug}`}
                          value={intervalValue}
                          onChange={(e) =>
                            setDrafts((d) => ({ ...d, [svc.slug]: e.target.value }))
                          }
                          onBlur={() => commitInterval(svc)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                          }}
                          className="w-20 rounded-md border border-hairline bg-surface-subtle px-2 py-1 text-sm text-ink"
                        />
                        <span className="text-xs text-muted">{t("admin.daemons.seconds")}</span>
                        {svc.interval_overridden ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => resetInterval(svc)}
                            data-testid={`daemon-interval-reset-${svc.slug}`}
                          >
                            {t("admin.daemons.reset")}
                          </Button>
                        ) : null}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 align-top text-xs text-muted">
                    {svc.last_ok_at ? formatDateTime(svc.last_ok_at, locale) : "—"}
                  </td>
                  <td className="px-3 py-2 align-top font-mono text-xs text-muted">
                    {svc.last_result ? JSON.stringify(svc.last_result) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-muted">{t("admin.daemons.docsHint")}</p>
    </div>
  );
}
