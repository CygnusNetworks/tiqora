/**
 * Shared bits of the change detail sections: UTC time text, `datetime-local` conversion, links
 * for the references the API returns (`ticket:12`, `ci:4`, …), error texts and the cache refresh
 * after a write.
 */
import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { changeErrorCode, changeErrorDetail, changeKeys, type Change, type ChangePhase } from "@/lib/changeApi";
import { changeDetailHref, ciDetailHref } from "@/lib/itsmRoutes";

export const FIELD_CLASS =
  "w-full rounded-lg border border-hairline bg-surface px-3 py-1.5 text-sm text-ink focus-visible:border-accent focus-visible:outline-none";

export const SECTION_CLASS = "min-w-0 space-y-2 rounded-md border border-hairline bg-surface p-3 text-sm";

/** Badge tone of a conflict or finding severity. */
export const SEVERITY_TONE = { info: "muted", warn: "warn", block: "danger" } as const;

/** Badge tone of a phase (list, calendar). */
export const PHASE_TONE: Record<ChangePhase, "default" | "accent" | "warn" | "danger" | "success" | "muted"> = {
  draft: "muted",
  submitted: "default",
  assessment: "default",
  approval: "warn",
  scheduled: "accent",
  implementing: "accent",
  review: "default",
  closed: "success",
  rejected: "danger",
  cancelled: "muted",
};

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

/** Outcomes after which a follow-up incident is offered ("Incident aus diesem Change erstellen", plan I13). */
export const FAILED_OUTCOMES: ReadonlySet<string> = new Set(["failed", "backed_out"]);

/** The new-ticket form prefilled for an incident about a failed change; the form links the two (`from_change`). */
export function incidentFromChangeHref(change: Pick<Change, "ticket_id" | "tn" | "title">, subject: string): string {
  const search = new URLSearchParams({ from_change: String(change.ticket_id), subject });
  return `/agent/tickets/new?${search.toString()}`;
}

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
  return useCallback(
    (change?: Change) => {
      if (change) qc.setQueryData(changeKeys.detail(ticketId), change);
      void qc.invalidateQueries({ queryKey: changeKeys.detail(ticketId) });
      void qc.invalidateQueries({ queryKey: [...changeKeys.all(), "list"] });
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId] });
    },
    [qc, ticketId],
  );
}
