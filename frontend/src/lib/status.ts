/** Ticket-state colour code, localised labels, and the "status spine" signature. */

export type EscalationLevel = "none" | "approaching" | "breached";

/**
 * Stable i18n key segment under `ticket.stateName.*` for a raw Znuny state name.
 * Covers stock Znuny compound names (`closed successful`, `pending reminder`, …).
 */
export type StateNameKey =
  | "new"
  | "open"
  | "pending"
  | "pendingReminder"
  | "pendingAuto"
  | "pendingAutoClosePlus"
  | "pendingAutoCloseMinus"
  | "closed"
  | "closedSuccessful"
  | "closedUnsuccessful"
  | "merged"
  | "removed";

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

/** Exact raw-name → i18n segment map (lowercase). */
const STATE_NAME_EXACT: Record<string, StateNameKey> = {
  new: "new",
  open: "open",
  pending: "pending",
  "pending reminder": "pendingReminder",
  "pending auto": "pendingAuto",
  "pending auto close+": "pendingAutoClosePlus",
  "pending auto close-": "pendingAutoCloseMinus",
  closed: "closed",
  "closed successful": "closedSuccessful",
  "closed unsuccessful": "closedUnsuccessful",
  merged: "merged",
  removed: "removed",
};

/** Longest-prefix first so compound names win over their short stems. */
const STATE_NAME_PREFIXES: Array<[string, StateNameKey]> = [
  ["pending auto close+", "pendingAutoClosePlus"],
  ["pending auto close-", "pendingAutoCloseMinus"],
  ["pending auto", "pendingAuto"],
  ["pending reminder", "pendingReminder"],
  ["closed successful", "closedSuccessful"],
  ["closed unsuccessful", "closedUnsuccessful"],
  ["pending", "pending"],
  ["closed", "closed"],
  ["open", "open"],
  ["new", "new"],
  ["merged", "merged"],
  ["removed", "removed"],
];

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
 * Map a raw Znuny state name (or state_type) to a `ticket.stateName.*` key segment.
 * Returns null when the name is unknown so callers can fall back to the raw string.
 */
export function stateNameKey(
  state: string | null | undefined,
): StateNameKey | null {
  if (!state) return null;
  const key = state.toLowerCase().trim();
  if (!key) return null;
  if (STATE_NAME_EXACT[key]) return STATE_NAME_EXACT[key];
  for (const [prefix, mapped] of STATE_NAME_PREFIXES) {
    if (key === prefix || key.startsWith(`${prefix} `) || key.startsWith(prefix)) {
      // Prefer exact prefix match; `startsWith(prefix)` also catches "pending auto close+"
      // when the exact map missed a spelling variant.
      if (key.startsWith(prefix)) return mapped;
    }
  }
  return null;
}

/** i18n key path for a raw state name, or null when unmapped. */
export function stateLabelI18nKey(
  state: string | null | undefined,
): `ticket.stateName.${StateNameKey}` | null {
  const key = stateNameKey(state);
  return key ? `ticket.stateName.${key}` : null;
}

/**
 * Localised display label for a Znuny state name. Falls back to the raw name
 * (or `fallback`) when no mapping exists — never invents a blank label.
 *
 * `t` is the react-i18next `t` function (or any compatible translator).
 */
export function stateLabel(
  t: (key: string, options?: { defaultValue?: string }) => string,
  state: string | null | undefined,
  fallback = "—",
): string {
  if (!state) return fallback;
  const i18nKey = stateLabelI18nKey(state);
  if (!i18nKey) return state;
  return t(i18nKey, { defaultValue: state });
}

/**
 * True when the ticket is in the "new" state — by `state_type` (preferred) or
 * by the raw state name. Used for the badge-only rendering rule.
 */
export function isNewTicketState(
  state: string | null | undefined,
  stateType?: string | null | undefined,
): boolean {
  if (stateType && stateType.toLowerCase().trim() === "new") return true;
  return stateNameKey(state) === "new";
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
