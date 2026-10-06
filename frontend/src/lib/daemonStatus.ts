import type { DaemonServiceOut } from "@/lib/api";

export type StatusColor = "green" | "amber" | "red" | "grey";

/** Shared between DaemonsPage's per-service status dot and the admin
 * dashboard's daemons KPI card so both agree on what counts as healthy. */
export function statusColor(svc: DaemonServiceOut, nowMs: number): StatusColor {
  if (!svc.enabled) return "grey";
  const lastOkMs = svc.last_ok_at ? new Date(svc.last_ok_at).getTime() : null;
  const lastRunMs = svc.last_run_at ? new Date(svc.last_run_at).getTime() : null;
  if (svc.last_error && (lastOkMs === null || (lastRunMs !== null && lastOkMs < lastRunMs))) {
    return "red";
  }
  if (lastOkMs === null) return "amber";
  // A long-polling tick (telegram getUpdates) blocks for tick_timeout_seconds
  // before its sleep even starts, so the real cadence is interval + timeout.
  const thresholdMs =
    svc.schedule === "daily"
      ? 26 * 3600 * 1000
      : ((svc.interval_seconds ?? 60) + svc.tick_timeout_seconds) * 3 * 1000;
  return nowMs - lastOkMs <= thresholdMs ? "green" : "amber";
}
