/**
 * Shared bits of the change detail sections: UTC time text, `datetime-local` conversion, links
 * for the references the API returns (`ticket:12`, `ci:4`, …), error texts and the cache refresh
 * after a write.
 */
import { useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { ApiError } from "@/lib/api";
import { changeErrorCode, changeKeys, type Change, type ChangePhase } from "@/lib/changeApi";
import { changeDetailHref, ciDetailHref } from "@/lib/itsmRoutes";

export const FIELD_CLASS =
  "w-full rounded-lg border border-hairline bg-surface px-3 py-1.5 text-sm text-ink focus-visible:border-accent focus-visible:outline-none";

export const SECTION_CLASS = "min-w-0 space-y-2 rounded-md border border-hairline bg-surface p-3 text-sm";

export const FINAL_PHASES: ReadonlySet<Change["phase"]> = new Set(["closed", "cancelled"]);

/** `2026-10-24T20:00:00Z` → `2026-10-24 20:00` (the API's times are UTC; the labels say so). */
export const utcText = (iso: string | null | undefined): string => (iso ? iso.slice(0, 16).replace("T", " ") : "");

export const windowText = (start: string | null | undefined, end: string | null | undefined): string =>
  start && end ? `${utcText(start)} – ${utcText(end)}` : "";

/** UTC instant → `datetime-local` value, and back. */
export const toLocalInput = (iso: string | null | undefined): string => (iso ? iso.slice(0, 16) : "");
export const fromLocalInput = (local: string): string | null => (local ? `${local}:00Z` : null);

/** The Znuny change list filtered by a ticket (Mode A: changes live in Znuny and are read only). */
export const legacyChangesHref = (ticketId: number) => `/agent/itsm/legacy-changes?ticket_id=${ticketId}`;

/** Button and dialog title of a step: the pair (`scheduled_draft` = "Neu planen") wins over the target. */
export function transitionLabel(from: ChangePhase, to: ChangePhase, t: TFunction): string {
  return t([`itsm.change.transition.pairs.${from}_${to}`, `itsm.change.transition.actions.${to}`]);
}

export type RefKind = "ticket" | "change" | "task" | "ci" | "service" | "freeze";

export function parseRef(ref: string | null | undefined): { kind: RefKind; id: number } | null {
  const m = /^(ticket|change|task|ci|service|freeze):(\d+)$/.exec(ref ?? "");
  return m ? { kind: m[1] as RefKind, id: Number(m[2]) } : null;
}

/** Where a reference opens; `null` for a freeze window (no agent page). The API only returns refs the caller may read. */
export function refHref(ref: string | null | undefined): string | null {
  const parsed = parseRef(ref);
  if (!parsed) return null;
  switch (parsed.kind) {
    case "ticket":
    case "task":
      return `/agent/tickets/${parsed.id}`;
    case "change":
      return changeDetailHref(parsed.id);
    case "ci":
      return ciDetailHref("tiqora", parsed.id);
    case "service":
      return `/agent/services?service_id=${parsed.id}`;
    default:
      return null;
  }
}

export function refLabel(ref: string, t: TFunction): string {
  const parsed = parseRef(ref);
  return parsed ? t(`itsm.change.refs.${parsed.kind}`, { id: parsed.id }) : ref;
}

/** `{detail: {...}}` of a failed change call. */
export function changeErrorDetail(err: unknown): Record<string, unknown> | null {
  if (!(err instanceof ApiError)) return null;
  const body = err.detail as { detail?: unknown } | null;
  const inner = body && typeof body === "object" && "detail" in body ? body.detail : body;
  return inner && typeof inner === "object" && !Array.isArray(inner) ? (inner as Record<string, unknown>) : null;
}

export type GuardFailure = { code: string; overridable: boolean };

/** The guard failures of a 409 `transition_refused`, else null. */
export function transitionFailures(err: unknown): GuardFailure[] | null {
  if (changeErrorCode(err) !== "transition_refused") return null;
  const failures = changeErrorDetail(err)?.failures;
  if (!Array.isArray(failures)) return [];
  return failures
    .filter((f): f is { code: string; overridable?: unknown } => !!f && typeof f.code === "string")
    .map((f) => ({ code: f.code, overridable: f.overridable === true }));
}

/** The text of a failed change write; the engine's own code is appended when the approval request failed. */
export function changeErrorText(err: unknown, t: TFunction): string {
  const code = changeErrorCode(err);
  const text = t([`itsm.change.errors.${code}`, "itsm.change.errors.generic"]);
  const engine = changeErrorDetail(err)?.engine_code;
  if (code === "approval_request_failed" && typeof engine === "string" && engine) {
    return `${text} ${t("itsm.change.errors.engineCode", { code: engine })}`;
  }
  return text;
}

/**
 * After a change write: store the answer (if any) and reload everything of the change and of its
 * ticket (`["tickets", id]` covers the zoom's ticket, ITSM summary and approvals).
 */
export function useChangeRefresh(ticketId: number) {
  const qc = useQueryClient();
  return (change?: Change) => {
    if (change) qc.setQueryData(changeKeys.detail(ticketId), change);
    void qc.invalidateQueries({ queryKey: changeKeys.detail(ticketId) });
    void qc.invalidateQueries({ queryKey: [...changeKeys.all(), "list"] });
    void qc.invalidateQueries({ queryKey: ["tickets", ticketId] });
  };
}
