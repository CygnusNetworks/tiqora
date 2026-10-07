import { callAnsweredAt, callEndedAt, removeCall } from "@/lib/callPopup";
import { phoneApi, type ActiveCall } from "@/lib/phoneApi";

/** Hide a call card here and in the agent's other tabs (best effort). */
export function dismissCall(callId: string) {
  removeCall(callId);
  phoneApi.dismissCall(callId).catch(() => {
    // already gone server-side (TTL) — the local removal is what matters
  });
}

/** New-ticket form search for logging `call` (the CTI popup's "new ticket"). */
export function phoneTicketSearchForCall(call: ActiveCall, customerLogin?: string) {
  return {
    type: "phone" as const,
    from_call: true,
    direction: call.direction,
    number: call.number.trim() || undefined,
    customer: customerLogin,
    call_started: callAnsweredAt(call) ?? undefined,
    call_ended: callEndedAt(call) ?? undefined,
    // The agent who answered becomes the owner (one agent per extension).
    owner_id: call.answered_by_user_id ?? undefined,
  };
}
