/** Bulk change draft for the queue list: picked fields → one PATCH per ticket. */

import type { MutationRequest } from "@/lib/api";
import type { SelectMenuItem } from "@/components/ui/SelectMenu";
import { addDaysYmd, fromZonedInputValue, wallClockDate, ymdInZone } from "@/lib/timeZone";

export type BulkField = "state" | "priority" | "owner" | "queue" | "lock";

export const BULK_FIELDS: BulkField[] = ["state", "priority", "owner", "queue", "lock"];

/** Fields the agent picked for a bulk change; unset = leave unchanged. */
export type BulkDraft = {
  state_id?: number;
  priority_id?: number;
  owner_id?: number;
  queue_id?: number;
  lock?: "lock" | "unlock";
  /** `datetime-local` value in the agent's display zone; only sent with a pending state. */
  pending_until?: string;
};

export type BulkState = { id: number; name: string; type_name: string };

export type BulkRefs = {
  states: BulkState[];
  priorities: { id: number; name: string }[];
  agents: SelectMenuItem<number>[];
  queues: SelectMenuItem<number>[];
};

/** Znuny's root user: owning a ticket as root is how "unassigned" is expressed. */
export const ROOT_USER_ID = 1;

const DRAFT_KEY: Record<BulkField, keyof BulkDraft> = {
  state: "state_id",
  priority: "priority_id",
  owner: "owner_id",
  queue: "queue_id",
  lock: "lock",
};

export function draftFields(draft: BulkDraft): BulkField[] {
  return BULK_FIELDS.filter((f) => draft[DRAFT_KEY[f]] != null);
}

export function clearDraftField(draft: BulkDraft, field: BulkField): BulkDraft {
  const next = { ...draft };
  delete next[DRAFT_KEY[field]];
  if (field === "state") delete next.pending_until;
  return next;
}

export function stateType(states: BulkState[], id: number | undefined): string {
  return states.find((s) => s.id === id)?.type_name ?? "";
}
export const isPendingStateId = (states: BulkState[], id: number | undefined) =>
  stateType(states, id).startsWith("pending");
export const isClosedStateId = (states: BulkState[], id: number | undefined) =>
  stateType(states, id).startsWith("closed");

/** One-tap reminder dates: 09:00 in the agent's zone, as `datetime-local` values. */
export function pendingQuickPicks(): { key: string; value: string }[] {
  const today = ymdInZone(new Date());
  const toMonday = (8 - wallClockDate().getDay()) % 7 || 7;
  return [
    { key: "pendingTomorrow", value: `${addDaysYmd(today, 1)}T09:00` },
    { key: "pendingMonday", value: `${addDaysYmd(today, toMonday)}T09:00` },
    { key: "pendingNextWeek", value: `${addDaysYmd(today, 7)}T09:00` },
  ];
}

/** True once at least one field is picked and a pending state has its date. */
export function draftReady(draft: BulkDraft, states: BulkState[]): boolean {
  if (draftFields(draft).length === 0) return false;
  if (isPendingStateId(states, draft.state_id)) return fromZonedInputValue(draft.pending_until) != null;
  return true;
}

/** The one PATCH body every selected ticket gets. */
export function draftToMutation(draft: BulkDraft, states: BulkState[]): MutationRequest {
  const body: MutationRequest = {};
  if (draft.queue_id != null) body.queue_id = draft.queue_id;
  if (draft.state_id != null) {
    body.state_id = draft.state_id;
    if (isPendingStateId(states, draft.state_id)) {
      body.pending_time = fromZonedInputValue(draft.pending_until)?.toISOString() ?? null;
    }
  }
  if (draft.priority_id != null) body.priority_id = draft.priority_id;
  if (draft.owner_id != null) body.owner_id = draft.owner_id;
  if (draft.lock != null) body.lock = draft.lock;
  return body;
}
