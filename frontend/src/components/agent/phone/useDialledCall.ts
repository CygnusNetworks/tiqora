import { useEffect, useState } from "react";
import { callAnsweredAt, callEndedAt, useCalls } from "@/lib/callPopup";
import type { DialledCall } from "@/lib/phoneCall";

/** Grace after the desk phone's ring timeout before an unanswered pickup
 * counts as "not connected" (the PBX reports nothing in that case). */
export const PICKUP_GRACE_MS = 5000;

export type DialPhase =
  /** the agent's desk phone rings */
  | "pickup"
  /** picked up, the PBX dials out */
  | "calling"
  | "connected"
  /** the call took place and is over */
  | "ended"
  /** never connected: hung up before the answer, or the desk phone was not picked up */
  | "failed";

export type DialProgress = {
  phase: DialPhase;
  extension: string | null;
  number: string;
  answeredAt: number | null;
  endedAt: number | null;
};

/**
 * Progress of a click-to-dial call from the CTI call state (`call_event`
 * SSE, see `lib/callPopup`): the PBX's `[tiqora-dial]` dialplan reports the
 * pickup (`ringing_at`), the answer and the hangup under the call id Tiqora
 * gave the channel. Null without a tracked call.
 */
export function useDialledCall(dialled: DialledCall | null | undefined): DialProgress | null {
  const calls = useCalls();
  const call = dialled ? calls.find((c) => c.call_id === dialled.callId) : undefined;
  const [expired, setExpired] = useState(false);

  const deadline = dialled ? dialled.at + dialled.ringTimeout * 1000 + PICKUP_GRACE_MS : null;
  const pickedUp = Boolean(call?.ringing_at);
  useEffect(() => {
    if (deadline === null || pickedUp) return undefined;
    const id = window.setTimeout(() => setExpired(true), Math.max(0, deadline - Date.now()));
    return () => window.clearTimeout(id);
  }, [deadline, pickedUp]);

  if (!dialled) return null;
  const answeredAt = call ? callAnsweredAt(call) : null;
  const endedAt = call ? callEndedAt(call) : null;
  let phase: DialPhase;
  if (call?.state === "ended") phase = answeredAt !== null ? "ended" : "failed";
  else if (answeredAt !== null) phase = "connected";
  else if (pickedUp) phase = "calling";
  else phase = expired ? "failed" : "pickup";
  return {
    phase,
    extension: call?.extension ?? null,
    number: call?.number ?? "",
    answeredAt,
    endedAt,
  };
}
