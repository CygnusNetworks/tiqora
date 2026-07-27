import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type SystemInfoOut } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { formatBytes, formatDateTime } from "@/lib/format";
import { statusColor, type StatusColor } from "@/lib/daemonStatus";

const QUERY_KEY = ["admin", "system-info"] as const;
const REFETCH_INTERVAL_MS = 10_000;

type Datastores = SystemInfoOut["datastores"];
type ContainerItem = NonNullable<SystemInfoOut["containers"]["items"]>[number];
type Host = SystemInfoOut["host"];

const DOT_CLASS: Record<StatusColor, string> = {
  green: "bg-green",
  amber: "bg-amber",
  red: "bg-danger",
  grey: "bg-muted",
};

const CHIP_CLASS: Record<StatusColor, string> = {
  green: "bg-green/15 text-green",
  amber: "bg-amber/15 text-amber",
  red: "bg-danger/15 text-danger",
  grey: "bg-muted/15 text-muted",
};

function StatusDot({ color }: { color: StatusColor }) {
  return <span className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${DOT_CLASS[color]}`} />;
}

function Chip({ color, children }: { color: StatusColor; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${CHIP_CLASS[color]}`}
    >
      {children}
    </span>
  );
}

/** Uppercase section header with a hairline rule and an optional tier badge. */
function SectionHead({ title, tier }: { title: string; tier?: { label: string; live: boolean } }) {
  return (
    <div className="mb-3 flex items-center gap-3">
      <h2 className="text-xs font-bold uppercase tracking-wider text-muted">{title}</h2>
      <span className="h-px flex-1 bg-hairline" />
      {tier ? (
        <span
          className={`whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
            tier.live ? "bg-green/15 text-green" : "bg-amber/15 text-amber"
          }`}
        >
          {tier.label}
        </span>
      ) : null}
    </div>
  );
}

function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-xl border border-hairline bg-surface p-4 shadow-[0_1px_2px_rgba(0,0,0,0.04)] ${className ?? ""}`}
    >
      {children}
    </div>
  );
}

function KeyRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1.5">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="text-right text-sm tabular-nums text-ink">{children}</dd>
    </div>
  );
}

/** Compact "18d 4h 11m" duration from seconds — locale-agnostic on purpose. */
function formatUptime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const parts: string[] = [];
  if (d) parts.push(`${d}d`);
  if (h || d) parts.push(`${h}h`);
  parts.push(`${m}m`);
  return parts.join(" ");
}

/** green < 70 %, amber < 90 %, red ≥ 90 % — for host resource meters. */
function meterColor(percent: number): StatusColor {
  if (percent >= 90) return "red";
  if (percent >= 70) return "amber";
  return "green";
}

const BAR_FILL: Record<StatusColor, string> = {
  green: "bg-green",
  amber: "bg-amber",
  red: "bg-danger",
  grey: "bg-muted",
};

function containerColor(c: ContainerItem): StatusColor {
  if (c.health === "healthy") return "green";
  if (c.health === "unhealthy") return "red";
  if (c.health === "starting") return "amber";
  return c.state === "running" ? "green" : "grey";
}

export function SystemInfoPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const infoQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => api.getSystemInfo(signal),
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  if (infoQ.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4" data-testid="admin-system-page">
        <Spinner />
      </div>
    );
  }

  if (infoQ.isError || !infoQ.data) {
    return (
      <div className="p-4 text-sm text-danger" data-testid="admin-system-page">
        {t("admin.systemInfo.loadError")}
      </div>
    );
  }

  const info = infoQ.data;
  const nowMs = Date.now();
  const { app, services, datastores, containers, host } = info;
  const containerItems = containers.items ?? [];

  const serviceColors = services.map((s) => statusColor(s, nowMs));
  const servicesOk = serviceColors.filter((c) => c === "green" || c === "grey").length;
  const datastoreStates: StatusColor[] = [
    datastores.database.connected ? "green" : "red",
    datastores.redis.connected ? "green" : "red",
    datastores.search.available ? "green" : "amber",
  ];

  const anyRed = serviceColors.includes("red") || datastoreStates.includes("red");
  const anyAmber = serviceColors.includes("amber") || datastoreStates.includes("amber");
  const overall: StatusColor = anyRed ? "red" : anyAmber ? "amber" : "green";
  const overallText =
    overall === "green"
      ? t("admin.systemInfo.overall.ok")
      : overall === "amber"
        ? t("admin.systemInfo.overall.degraded")
        : t("admin.systemInfo.overall.error");

  const tierLive = { label: t("admin.systemInfo.tier.live"), live: true };
  const tierOptin = (what: string) => ({
    label: `${t("admin.systemInfo.tier.optin")} · ${what}`,
    live: false,
  });

  return (
    <div className="mx-auto max-w-5xl space-y-7 p-4" data-testid="admin-system-page">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">
            {t("admin.systemInfo.title")}
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">{t("admin.systemInfo.description")}</p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-accent/15 px-3 py-1 text-xs font-semibold text-accent">
          {app.environment}
        </span>
      </div>

      {/* Overall banner */}
      <div
        className={`flex flex-wrap items-center gap-4 rounded-xl border p-4 ${
          overall === "green"
            ? "border-green/30 bg-green/5"
            : overall === "amber"
              ? "border-amber/30 bg-amber/5"
              : "border-danger/30 bg-danger/5"
        }`}
        data-testid="system-overall"
        data-status={overall}
      >
        <span className={`grid h-8 w-8 place-items-center rounded-full ${DOT_CLASS[overall]}/20`}>
          <StatusDot color={overall} />
        </span>
        <div className="min-w-0">
          <p className="font-semibold text-ink">{overallText}</p>
          <p className="text-xs text-muted">
            {t("admin.systemInfo.overall.uptime", { duration: formatUptime(app.uptime_seconds) })}
          </p>
        </div>
        <div className="ml-auto flex flex-wrap gap-6 text-right">
          <div>
            <div className="text-lg font-semibold tabular-nums text-ink">
              {servicesOk}&thinsp;/&thinsp;{services.length}
            </div>
            <div className="text-[11px] uppercase tracking-wide text-muted">
              {t("admin.systemInfo.sections.services")}
            </div>
          </div>
          <div>
            <div className="text-lg font-semibold tabular-nums text-ink">
              {datastoreStates.filter((c) => c === "green").length}&thinsp;/&thinsp;3
            </div>
            <div className="text-[11px] uppercase tracking-wide text-muted">
              {t("admin.systemInfo.sections.datastores")}
            </div>
          </div>
          {containers.available ? (
            <div>
              <div className="text-lg font-semibold tabular-nums text-ink">
                {containerItems.filter((c) => containerColor(c) === "green").length}&thinsp;/&thinsp;
                {containerItems.length}
              </div>
              <div className="text-[11px] uppercase tracking-wide text-muted">
                {t("admin.systemInfo.sections.containers")}
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {/* Application & Build */}
      <section>
        <SectionHead title={t("admin.systemInfo.sections.app")} tier={tierLive} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Card>
            <dl className="divide-y divide-hairline">
              <KeyRow label={t("admin.systemInfo.app.product")}>{app.name}</KeyRow>
              <KeyRow label={t("admin.systemInfo.app.version")}>
                <span className="font-mono text-xs">{app.version}</span>
              </KeyRow>
              <KeyRow label={t("admin.systemInfo.app.gitSha")}>
                <span className="font-mono text-xs">{app.git_sha ?? "—"}</span>
              </KeyRow>
              <KeyRow label={t("admin.systemInfo.app.buildTime")}>
                {app.build_time ? formatDateTime(app.build_time, locale) : "—"}
              </KeyRow>
              <KeyRow label={t("admin.systemInfo.app.environment")}>{app.environment}</KeyRow>
            </dl>
          </Card>
          <Card>
            <dl className="divide-y divide-hairline">
              <KeyRow label={t("admin.systemInfo.app.python")}>
                <span className="font-mono text-xs">{app.python_version}</span>
              </KeyRow>
              <KeyRow label={t("admin.systemInfo.app.hostname")}>
                <span className="font-mono text-xs">{app.hostname}</span>
              </KeyRow>
              <KeyRow label={t("admin.systemInfo.app.uptime")}>
                {formatUptime(app.uptime_seconds)}
              </KeyRow>
              <KeyRow label={t("admin.systemInfo.app.serverTime")}>
                {formatDateTime(app.server_time, locale)}
              </KeyRow>
            </dl>
          </Card>
        </div>
      </section>

      {/* Background services */}
      <section>
        <SectionHead title={t("admin.systemInfo.sections.services")} tier={tierLive} />
        <div className="overflow-x-auto rounded-xl border border-hairline bg-surface">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-hairline text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-4 py-2.5">{t("admin.systemInfo.services.service")}</th>
                <th className="px-4 py-2.5">{t("admin.systemInfo.services.status")}</th>
                <th className="px-4 py-2.5">{t("admin.systemInfo.services.lastOk")}</th>
                <th className="px-4 py-2.5 text-right">{t("admin.systemInfo.services.message")}</th>
              </tr>
            </thead>
            <tbody>
              {services.map((svc, i) => {
                const color = serviceColors[i];
                return (
                  <tr
                    key={svc.slug}
                    className="border-b border-hairline last:border-0"
                    data-testid={`system-service-${svc.slug}`}
                  >
                    <td className="px-4 py-2.5 font-medium text-ink">
                      {t(`admin.daemons.services.${svc.slug}.name`)}
                    </td>
                    <td className="px-4 py-2.5" data-status={color}>
                      <Chip color={color}>
                        <StatusDot color={color} />
                        {t(`admin.daemons.status.${color}`)}
                      </Chip>
                    </td>
                    <td className="px-4 py-2.5 text-xs tabular-nums text-muted">
                      {svc.last_ok_at ? formatDateTime(svc.last_ok_at, locale) : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-right text-xs text-muted">
                      {svc.last_error ? (
                        <span className="text-danger">{svc.last_error}</span>
                      ) : (
                        <ResultSummary result={svc.last_result} />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Datastores & search */}
      <section>
        <SectionHead title={t("admin.systemInfo.sections.datastores")} tier={tierLive} />
        <DatastoresCards datastores={datastores} />
      </section>

      {/* Containers */}
      <section>
        <SectionHead
          title={t("admin.systemInfo.sections.containers")}
          tier={tierOptin(t("admin.systemInfo.tier.dockerSocket"))}
        />
        {containers.available ? (
          <>
            {containers.engine_version ? (
              <p className="mb-3 text-xs text-muted">
                {t("admin.systemInfo.containers.engine")}{" "}
                <span className="font-mono text-ink">{containers.engine_version}</span>
                {" · "}
                {t("admin.systemInfo.containers.running", {
                  count: containerItems.filter((c) => containerColor(c) === "green").length,
                  total: containerItems.length,
                })}
              </p>
            ) : null}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {containerItems.map((c) => {
              const color = containerColor(c);
              return (
                <Card key={c.name} className="flex flex-col gap-2">
                  <div className="flex items-center gap-2">
                    <StatusDot color={color} />
                    <b className="truncate font-semibold text-ink">{c.name}</b>
                    <span className="ml-auto">
                      <Chip color={color}>{c.health ?? c.state}</Chip>
                    </span>
                  </div>
                  <div className="break-all font-mono text-[11px] text-muted">{c.image}</div>
                  <div className="flex gap-4 text-xs tabular-nums text-muted">
                    {c.started_at ? (
                      <span>
                        {t("admin.systemInfo.containers.since")}{" "}
                        {formatDateTime(c.started_at, locale)}
                      </span>
                    ) : null}
                    {c.restart_count != null ? (
                      <span>
                        {t("admin.systemInfo.containers.restarts")}: {c.restart_count}
                      </span>
                    ) : null}
                  </div>
                </Card>
              );
            })}
            </div>
          </>
        ) : (
          <UnavailableNote
            configured={containers.configured}
            reason={containers.reason}
            hint={t("admin.systemInfo.containers.notConfiguredHint")}
          />
        )}
      </section>

      {/* Host resources */}
      <section>
        <SectionHead
          title={t("admin.systemInfo.sections.host")}
          tier={tierOptin(t("admin.systemInfo.tier.psutil"))}
        />
        {host.available ? (
          <HostMeters host={host} />
        ) : (
          <UnavailableNote
            configured={host.configured}
            reason={host.reason}
            hint={t("admin.systemInfo.host.notConfiguredHint")}
          />
        )}
      </section>
    </div>
  );
}

function DatastoresCards({ datastores }: { datastores: Datastores }) {
  const { t } = useTranslation();
  const { database: db, redis, search } = datastores;
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <Card className="space-y-3">
        <div className="flex items-center gap-2">
          <StatusDot color={db.connected ? "green" : "red"} />
          <span className="font-semibold text-ink">{db.dialect}</span>
          {db.version ? (
            <span className="ml-auto truncate font-mono text-[11px] text-muted" title={db.version}>
              {db.version.split(" ").slice(0, 2).join(" ")}
            </span>
          ) : null}
        </div>
        <Chip color={db.connected ? "green" : "red"}>
          {db.connected
            ? t("admin.systemInfo.db.connected")
            : t("admin.systemInfo.db.disconnected")}
        </Chip>
        <div className="flex flex-wrap gap-4">
          <Metric label={t("admin.systemInfo.db.size")} value={fmtBytesOrDash(db.size_bytes)} />
          <Metric
            label={t("admin.systemInfo.db.latency")}
            value={db.latency_ms != null ? `${db.latency_ms} ms` : "—"}
          />
        </div>
      </Card>

      <Card className="space-y-3">
        <div className="flex items-center gap-2">
          <StatusDot color={redis.connected ? "green" : "red"} />
          <span className="font-semibold text-ink">Redis</span>
          {redis.version ? (
            <span className="ml-auto font-mono text-[11px] text-muted">{redis.version}</span>
          ) : null}
        </div>
        <Chip color={redis.connected ? "green" : "red"}>
          {redis.connected
            ? t("admin.systemInfo.db.connected")
            : t("admin.systemInfo.db.disconnected")}
        </Chip>
        <div className="flex flex-wrap gap-4">
          <Metric
            label={t("admin.systemInfo.redis.memory")}
            value={fmtBytesOrDash(redis.used_memory_bytes)}
          />
          <Metric
            label={t("admin.systemInfo.redis.clients")}
            value={redis.clients != null ? String(redis.clients) : "—"}
          />
          <Metric
            label={t("admin.systemInfo.db.latency")}
            value={redis.latency_ms != null ? `${redis.latency_ms} ms` : "—"}
          />
        </div>
      </Card>

      <Card className="space-y-3">
        <div className="flex items-center gap-2">
          <StatusDot color={search.available ? "green" : "amber"} />
          <span className="font-semibold text-ink">Meilisearch</span>
          {search.version ? (
            <span className="ml-auto font-mono text-[11px] text-muted">{search.version}</span>
          ) : null}
        </div>
        {search.available ? (
          <>
            <Chip color="green">{t("admin.systemInfo.search.available")}</Chip>
            <div className="flex flex-wrap gap-4">
              <Metric
                label={t("admin.systemInfo.search.tickets")}
                value={numOrDash(search.tickets_docs)}
              />
              <Metric label={t("admin.systemInfo.search.kb")} value={numOrDash(search.kb_docs)} />
              <Metric
                label={t("admin.systemInfo.search.indexSize")}
                value={fmtBytesOrDash(search.database_size_bytes)}
              />
            </div>
          </>
        ) : (
          <p className="text-xs text-amber">
            {search.reason ?? t("admin.systemInfo.search.unavailable")}
          </p>
        )}
      </Card>
    </div>
  );
}

function HostMeters({ host }: { host: Host }) {
  const { t } = useTranslation();
  const meters: { label: string; percent: number | null | undefined; sub: string }[] = [
    {
      label: t("admin.systemInfo.host.cpu"),
      percent: host.cpu_percent,
      sub:
        host.cpu_count != null
          ? t("admin.systemInfo.host.cores", {
              count: host.cpu_count,
              load: host.load_avg ? host.load_avg.join(" / ") : "—",
            })
          : "",
    },
    {
      label: t("admin.systemInfo.host.memory"),
      percent: host.memory_percent,
      sub:
        host.memory_used_bytes != null && host.memory_total_bytes != null
          ? `${formatBytes(host.memory_used_bytes)} / ${formatBytes(host.memory_total_bytes)}`
          : "",
    },
    {
      label: `${t("admin.systemInfo.host.disk")} ${host.disk_path ?? ""}`.trim(),
      percent: host.disk_percent,
      sub:
        host.disk_used_bytes != null && host.disk_total_bytes != null
          ? `${formatBytes(host.disk_used_bytes)} / ${formatBytes(host.disk_total_bytes)}`
          : "",
    },
  ];
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      {meters.map((m) => {
        const pct = m.percent ?? 0;
        const color = meterColor(pct);
        return (
          <Card key={m.label} className="space-y-2">
            <div className="flex items-baseline justify-between">
              <b className="text-sm text-ink">{m.label}</b>
              <span className="text-xs tabular-nums text-muted">{Math.round(pct)} %</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-surface-subtle">
              <div
                className={`h-full rounded-full ${BAR_FILL[color]}`}
                style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
              />
            </div>
            {m.sub ? <p className="text-[11px] tabular-nums text-muted">{m.sub}</p> : null}
          </Card>
        );
      })}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="text-base font-semibold tabular-nums text-ink">{value}</div>
    </div>
  );
}

/**
 * Neutral note for an optional section that isn't showing data. Separates
 * "opt-in simply not set up" (configured=false → a calm hint) from a real
 * probe error (reason set → shown as-is).
 */
function UnavailableNote({
  configured = true,
  reason,
  hint,
}: {
  configured?: boolean;
  reason?: string | null;
  hint?: string;
}) {
  const { t } = useTranslation();
  const notConfigured = configured === false;
  return (
    <Card className="border-dashed">
      {notConfigured ? (
        <div>
          <p className="text-sm font-medium text-muted">
            {t("admin.systemInfo.notConfigured")}
          </p>
          {hint ? <p className="mt-0.5 text-xs text-muted">{hint}</p> : null}
        </div>
      ) : (
        <p className="text-sm text-muted">{reason ?? t("admin.systemInfo.unavailable")}</p>
      )}
    </Card>
  );
}

/** Compact key/value rendering of a daemon's last structured result (was raw JSON). */
function ResultSummary({ result }: { result?: Record<string, unknown> | null }) {
  if (!result || typeof result !== "object") return <>—</>;
  const entries = Object.entries(result);
  if (entries.length === 0) return <>—</>;
  return (
    <span className="inline-flex flex-wrap justify-end gap-1">
      {entries.map(([k, v]) => (
        <span
          key={k}
          className="inline-flex items-center gap-1 rounded bg-surface-subtle px-1.5 py-0.5"
        >
          <span className="text-muted">{k}</span>
          <span className="font-mono tabular-nums text-ink">{formatResultValue(v)}</span>
        </span>
      ))}
    </span>
  );
}

function formatResultValue(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function fmtBytesOrDash(v: number | null | undefined): string {
  return v != null ? formatBytes(v) : "—";
}

function numOrDash(v: number | null | undefined): string {
  return v != null ? v.toLocaleString() : "—";
}
