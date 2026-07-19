/** Ticket-state colour code and the "status spine" signature element. */

export type EscalationLevel = "none" | "approaching" | "breached";

const STATE_COLOR_VARS: Record<string, string> = {
  new: "var(--color-state-new)",
  open: "var(--color-state-open)",
  pending: "var(--color-state-pending)",
  "pending reminder": "var(--color-state-pending)",
  "pending auto": "var(--color-state-pending)",
  closed: "var(--color-state-closed)",
  "closed successful": "var(--color-state-closed)",
  "closed unsuccessful": "var(--color-state-closed)",
  merged: "var(--color-state-removed)",
  removed: "var(--color-state-removed)",
};

/** Resolve a CSS colour value for a ticket state name (case-insensitive, prefix match). */
export function stateColorVar(state: string | null | undefined): string {
  if (!state) return "var(--color-hairline)";
  const key = state.toLowerCase().trim();
  if (STATE_COLOR_VARS[key]) return STATE_COLOR_VARS[key];
  for (const [prefix, value] of Object.entries(STATE_COLOR_VARS)) {
    if (key.startsWith(prefix)) return value;
  }
  return "var(--color-hairline)";
}

/**
 * Escalation level for the spine: "breached" once the escalation epoch has
 * passed, "approaching" inside the warning window (default 30 min), else "none".
 */
export function escalationLevel(
  epochSeconds: number | null | undefined,
  warningWindowSeconds = 30 * 60,
): EscalationLevel {
  if (!epochSeconds || epochSeconds <= 0) return "none";
  const remaining = epochSeconds * 1000 - Date.now();
  if (remaining <= 0) return "breached";
  if (remaining <= warningWindowSeconds * 1000) return "approaching";
  return "none";
}

/** Merge several escalation timestamps into the single most-severe level. */
export function combinedEscalationLevel(
  epochs: Array<number | null | undefined>,
  warningWindowSeconds = 30 * 60,
): EscalationLevel {
  let level: EscalationLevel = "none";
  for (const epoch of epochs) {
    const l = escalationLevel(epoch, warningWindowSeconds);
    if (l === "breached") return "breached";
    if (l === "approaching") level = "approaching";
  }
  return level;
}

/** Human-readable mono countdown badge text for a breached/approaching escalation. */
export function formatCountdown(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "";
  const diffMs = epochSeconds * 1000 - Date.now();
  const abs = Math.abs(Math.round(diffMs / 60000));
  const sign = diffMs < 0 ? "-" : "";
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  const body = h > 0 ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m`;
  return `${sign}${body}`;
}

export function spineClassName(level: EscalationLevel): string {
  if (level === "breached") return "status-spine status-spine--escalation-breached";
  if (level === "approaching") return "status-spine status-spine--escalation-approaching";
  return "status-spine";
}
