import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { api, type DaemonServiceOut, type DaemonUpdate } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { formatAgeSeconds, formatDateTime } from "@/lib/format";
import { statusColor, type StatusColor } from "@/lib/daemonStatus";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { cn } from "@/lib/cn";

const QUERY_KEY = ["admin", "daemons"] as const;
const REFETCH_INTERVAL_MS = 10_000;

//: postmaster/escalation/notifications/generic_agent are Znuny daemon
// takeovers and must stay mutually exclusive with the corresponding Znuny
// scheduler task — see docs/parallel-operation.md.
const TAKEOVER_SLUGS = new Set(["postmaster", "escalation", "notifications", "generic_agent"]);

const DOT_CLASS: Record<StatusColor, string> = {
  green: "bg-green",
  amber: "bg-amber",
  red: "bg-danger",
  grey: "bg-muted",
};

/** Status word color + dot halo per health color ("Variante B" restyle). */
const STATUS_TEXT_CLASS: Record<StatusColor, string> = {
  green: "text-green",
  amber: "text-amber",
  red: "text-danger",
  grey: "text-muted",
};
const DOT_HALO_CLASS: Record<StatusColor, string> = {
  green: "shadow-[0_0_0_3px] shadow-green/15",
  amber: "shadow-[0_0_0_3px] shadow-amber/15",
  red: "shadow-[0_0_0_3px] shadow-danger/15",
  grey: "",
};

/** Compact humanized form of the raw last_result JSON — "processed: 12" reads
 * better in a pill than {"processed":12}. Non-objects fall back to String(). */
function humanizeResult(result: unknown): string {
  if (result == null) return "";
  if (typeof result === "object" && !Array.isArray(result)) {
    const entries = Object.entries(result as Record<string, unknown>);
    if (entries.length > 0) {
      return entries.map(([k, v]) => `${k}: ${String(v)}`).join(" · ");
    }
  }
  return JSON.stringify(result);
}

/** Styled on/off switch backed by a real (screen-reader-only) checkbox so
 * existing tests and a11y semantics (checked/disabled) keep working. */
function Switch({
  checked,
  disabled,
  onChange,
  testId,
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: () => void;
  testId: string;
  label: string;
}) {
  return (
    <label
      className={cn(
        "relative inline-flex shrink-0 items-center",
        disabled ? "cursor-not-allowed" : "cursor-pointer",
      )}
    >
      <input
        type="checkbox"
        className="peer sr-only"
        data-testid={testId}
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        aria-label={label}
      />
      <span
        className={cn(
          "relative h-5 w-[34px] rounded-full bg-hairline transition-colors duration-150",
          "after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:shadow after:transition-transform after:duration-150",
          "peer-checked:bg-accent peer-checked:after:translate-x-3.5",
          "peer-disabled:opacity-50",
          "peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent",
        )}
        aria-hidden
      />
    </label>
  );
}

export function DaemonsPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
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

      <div className="overflow-x-auto rounded-xl border border-hairline bg-surface">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-hairline bg-surface-subtle text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">{t("admin.daemons.columns.status")}</th>
              <th className="px-3 py-2 font-medium">{t("admin.daemons.columns.service")}</th>
              <th className="px-3 py-2 font-medium">
                <span className="inline-flex items-center gap-1.5">
                  {t("admin.daemons.columns.schedule")}
                  <HelpPopover title={t("admin.daemons.columns.schedule")} testId="daemons-help-schedule">
                    {t("admin.help.daemons.schedule")}
                  </HelpPopover>
                </span>
              </th>
              <th className="px-3 py-2 font-medium">{t("admin.daemons.columns.lastOk")}</th>
              <th className="px-3 py-2 font-medium">{t("admin.daemons.columns.lastResult")}</th>
              <th className="px-3 py-2 font-medium">
                <span className="inline-flex items-center gap-1.5">
                  {t("admin.daemons.columns.enabled")}
                  <HelpPopover title={t("admin.daemons.columns.enabled")} testId="daemons-help-enabled">
                    {t("admin.help.daemons.enabled")}
                  </HelpPopover>
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {services.map((svc) => {
              const color = statusColor(svc, nowMs);
              const hasError = svc.enabled && Boolean(svc.last_error);
              const draft = drafts[svc.slug];
              const intervalValue = draft ?? String(svc.interval_seconds ?? "");
              const lastOkAgeS = svc.last_ok_at
                ? (nowMs - new Date(svc.last_ok_at).getTime()) / 1000
                : null;
              const resultText = humanizeResult(svc.last_result);
              return (
                <Fragment key={svc.slug}>
                  <tr
                    className={cn(
                      "border-b border-hairline last:border-0",
                      hasError ? "border-b-0 bg-danger/5" : "hover:bg-surface-subtle/60",
                    )}
                    data-testid={`daemon-row-${svc.slug}`}
                  >
                    <td className="whitespace-nowrap px-3 py-2.5 align-top">
                      {/*
                        Status is runtime health only. When disabled, Aktiv already
                        carries the config state — don't also label status "Deaktiviert".
                      */}
                      <span
                        className={cn(
                          "inline-flex items-center gap-2 text-[12.5px] font-semibold",
                          STATUS_TEXT_CLASS[color],
                        )}
                        data-testid={`daemon-status-${svc.slug}`}
                        data-status={color}
                      >
                        {svc.enabled ? (
                          <>
                            <span
                              className={cn(
                                "h-2 w-2 shrink-0 rounded-full",
                                DOT_CLASS[color],
                                DOT_HALO_CLASS[color],
                              )}
                            />
                            {t(`admin.daemons.status.${color}`)}
                          </>
                        ) : (
                          <span className="font-normal text-muted">—</span>
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 align-top">
                      <div className="flex items-center gap-1.5 font-semibold text-ink">
                        {t(`admin.daemons.services.${svc.slug}.name`)}
                        {TAKEOVER_SLUGS.has(svc.slug) && (
                          <span className="inline-flex items-center gap-1 rounded-md border border-purple/30 bg-purple/10 px-1.5 py-0.5 text-[10.5px] font-semibold text-purple">
                            {t("admin.daemons.takeoverBadge")}
                            <HelpPopover
                              title={t(`admin.daemons.services.${svc.slug}.name`)}
                              testId={`daemons-help-takeover-${svc.slug}`}
                            >
                              {t("admin.help.daemons.takeover")}
                            </HelpPopover>
                          </span>
                        )}
                      </div>
                      <div className="mt-0.5 text-xs text-muted">
                        {t(`admin.daemons.services.${svc.slug}.description`)}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 align-top">
                      {svc.schedule === "daily" ? (
                        <span className="inline-flex items-center rounded-lg border border-hairline bg-surface-subtle px-2 py-1 text-xs text-ink">
                          {t("admin.daemons.dailyAt", { time: svc.daily_at })}
                        </span>
                      ) : svc.slug === "poller" ? (
                        <span className="inline-flex items-center rounded-lg border border-hairline bg-surface-subtle px-2 py-1 text-xs text-ink">
                          {t("admin.daemons.intervalSeconds", { seconds: svc.interval_seconds })}
                        </span>
                      ) : (
                        <div className="flex items-center gap-1.5">
                          <div className="inline-flex items-center gap-1 rounded-lg border border-hairline bg-surface-subtle px-2 py-1">
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
                              className="w-14 bg-transparent text-xs text-ink [appearance:textfield] focus:outline-none [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                            />
                            <span className="text-[11px] text-muted">
                              {t("admin.daemons.seconds")}
                            </span>
                          </div>
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
                    <td
                      className={cn(
                        "whitespace-nowrap px-3 py-2.5 align-top text-xs tabular-nums",
                        hasError ? "text-danger" : "text-muted",
                      )}
                      title={svc.last_ok_at ? formatDateTime(svc.last_ok_at, locale) : undefined}
                    >
                      {lastOkAgeS != null ? formatAgeSeconds(lastOkAgeS, locale) : "—"}
                    </td>
                    <td className="px-3 py-2.5 align-top">
                      {resultText ? (
                        <span
                          className="inline-flex max-w-[16rem] items-center truncate rounded-full border border-hairline bg-surface-subtle px-2.5 py-0.5 font-mono text-[11px] text-ink"
                          title={JSON.stringify(svc.last_result)}
                        >
                          {resultText}
                        </span>
                      ) : (
                        <span className="text-xs text-muted">—</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 align-top">
                      <span className="inline-flex items-center gap-2">
                        <Switch
                          checked={svc.enabled}
                          disabled={!svc.toggleable || updateM.isPending}
                          onChange={() => toggle(svc)}
                          testId={`daemon-toggle-${svc.slug}`}
                          label={t("admin.daemons.columns.enabled")}
                        />
                        {!svc.toggleable ? (
                          <span className="rounded bg-surface-subtle px-1.5 py-0.5 text-[11px] font-medium text-muted">
                            {t("admin.daemons.alwaysOn")}
                          </span>
                        ) : null}
                      </span>
                    </td>
                  </tr>
                  {hasError && (
                    <tr
                      className="border-b border-hairline bg-danger/5 last:border-0"
                      data-testid={`daemon-error-${svc.slug}`}
                    >
                      <td />
                      <td colSpan={5} className="px-3 pb-2.5 pt-0 text-xs text-danger">
                        ⚠ {svc.last_error}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-muted">{t("admin.daemons.docsHint")}</p>
    </div>
  );
}
