import { useSyncExternalStore } from "react";
import type { ActiveCall } from "./phoneApi";

/**
 * State of the CTI call popup (`CallPopup`): the calls the PBX reported for
 * this agent, fed by the SSE `call_event` message and restored after a reload
 * from `GET /phone/calls/active`. The backend already addresses events to the
 * agents whose extension rang; a call whose `user_ids` no longer contain the
 * viewer (answered by a colleague) is hidden by `visibleCalls`.
 */

export type CallEventName = "ringing" | "answered" | "hangup" | "handled" | "dismissed";

export type CallEventMessage = {
  type: "call_event";
  user_ids: number[];
  event: CallEventName;
  call: ActiveCall;
};

/** An ended call stays offered for logging this long. */
export const ENDED_CALL_TTL_MS = 15 * 60 * 1000;

const time = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
};

export const callAnsweredAt = (call: ActiveCall) => time(call.answered_at);
export const callEndedAt = (call: ActiveCall) => time(call.ended_at);

/** Seconds on the call so far (0 until answered; fixed once ended). */
export function callDurationSeconds(call: ActiveCall, now: number = Date.now()): number {
  const answered = callAnsweredAt(call);
  if (answered === null) return 0;
  const end = callEndedAt(call) ?? now;
  return Math.max(0, Math.floor((end - answered) / 1000));
}

/** Apply one SSE `call_event` to the list (newest first). */
export function applyCallEvent(calls: ActiveCall[], message: CallEventMessage): ActiveCall[] {
  const rest = calls.filter((c) => c.call_id !== message.call.call_id);
  if (message.event === "dismissed") return rest;
  const existing = calls.find((c) => c.call_id === message.call.call_id);
  return existing
    ? calls.map((c) => (c.call_id === message.call.call_id ? message.call : c))
    : [message.call, ...rest];
}

/** Calls the viewer should see: theirs, and ended ones only for 15 minutes.
 * Click-to-dial calls belong to the phone-call form, not the popup. */
export function visibleCalls(
  calls: ActiveCall[],
  userId: number | null | undefined,
  now: number = Date.now(),
): ActiveCall[] {
  return calls.filter((c) => {
    if (c.click_to_dial) return false;
    if (userId != null && !c.user_ids.includes(userId)) return false;
    if (c.state !== "ended") return true;
    const ended = callEndedAt(c);
    return ended !== null && now - ended <= ENDED_CALL_TTL_MS;
  });
}

// ---------------------------------------------------------------------------
// Module store (same pattern as notificationStore)
// ---------------------------------------------------------------------------

let calls: ActiveCall[] = [];
const listeners = new Set<() => void>();

function setCalls(next: ActiveCall[]): void {
  calls = next;
  for (const listener of listeners) listener();
}

export function getCalls(): ActiveCall[] {
  return calls;
}

export function receiveCallEvent(message: CallEventMessage): void {
  setCalls(applyCallEvent(calls, message));
}

/** Replace with the server's view (reload restore). Calls that arrived over
 * SSE meanwhile and are unknown to the snapshot are kept. */
export function restoreCalls(fromServer: ActiveCall[]): void {
  const known = new Set(fromServer.map((c) => c.call_id));
  setCalls([...fromServer, ...calls.filter((c) => !known.has(c.call_id))]);
}

export function removeCall(callId: string): void {
  setCalls(calls.filter((c) => c.call_id !== callId));
}

export function resetCalls(): void {
  setCalls([]);
}

function subscribe(callback: () => void): () => void {
  listeners.add(callback);
  return () => {
    listeners.delete(callback);
  };
}

export function useCalls(): ActiveCall[] {
  return useSyncExternalStore(
    subscribe,
    () => calls,
    () => calls,
  );
}
