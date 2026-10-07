import { beforeEach, describe, expect, it } from "vitest";
import {
  applyCallEvent,
  callDurationSeconds,
  getCalls,
  receiveCallEvent,
  resetCalls,
  restoreCalls,
  visibleCalls,
  type CallEventMessage,
} from "./callPopup";
import type { ActiveCall } from "./phoneApi";

const T0 = Date.parse("2026-09-29T10:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();

function call(overrides: Partial<ActiveCall> = {}): ActiveCall {
  return {
    call_id: "c1",
    click_to_dial: false,
    state: "ringing",
    number: "+492285550101",
    extension: "100",
    direction: "inbound",
    user_ids: [7],
    ringing_at: iso(T0),
    answered_at: null,
    ended_at: null,
    ...overrides,
  };
}

const msg = (event: CallEventMessage["event"], c: ActiveCall): CallEventMessage => ({
  type: "call_event",
  user_ids: c.user_ids,
  event,
  call: c,
});

describe("call popup state", () => {
  beforeEach(() => resetCalls());

  it("walks ringing → answered → hangup on one card", () => {
    let calls = applyCallEvent([], msg("ringing", call()));
    expect(calls).toHaveLength(1);
    calls = applyCallEvent(calls, msg("answered", call({ state: "answered", answered_at: iso(T0 + 5000) })));
    expect(calls).toHaveLength(1);
    expect(calls[0].state).toBe("answered");
    expect(callDurationSeconds(calls[0], T0 + 65_000)).toBe(60);
    calls = applyCallEvent(
      calls,
      msg("hangup", call({ state: "ended", answered_at: iso(T0 + 5000), ended_at: iso(T0 + 95_000) })),
    );
    expect(calls[0].state).toBe("ended");
    expect(callDurationSeconds(calls[0], T0 + 999_000)).toBe(90);
  });

  it("stacks calls newest first and removes a dismissed one", () => {
    let calls = applyCallEvent([], msg("ringing", call()));
    calls = applyCallEvent(calls, msg("ringing", call({ call_id: "c2" })));
    expect(calls.map((c) => c.call_id)).toEqual(["c2", "c1"]);
    calls = applyCallEvent(calls, msg("dismissed", call()));
    expect(calls.map((c) => c.call_id)).toEqual(["c2"]);
  });

  it("hides calls answered by a colleague, click-to-dial calls and ended calls after 15 minutes", () => {
    const calls = [
      call({ call_id: "mine" }),
      call({ call_id: "taken", state: "answered", user_ids: [8] }),
      call({ call_id: "recent", state: "ended", ended_at: iso(T0) }),
      call({ call_id: "dialled", direction: "outbound", click_to_dial: true }),
    ];
    expect(visibleCalls(calls, 7, T0 + 60_000).map((c) => c.call_id)).toEqual(["mine", "recent"]);
    expect(visibleCalls(calls, 7, T0 + 16 * 60_000).map((c) => c.call_id)).toEqual(["mine"]);
  });

  it("missed calls have no duration", () => {
    expect(callDurationSeconds(call({ state: "ended", ended_at: iso(T0 + 1000) }), T0 + 5000)).toBe(0);
  });

  it("restore keeps SSE calls unknown to the snapshot", () => {
    receiveCallEvent(msg("ringing", call({ call_id: "live" })));
    receiveCallEvent(msg("ringing", call({ call_id: "old", state: "ringing" })));
    restoreCalls([call({ call_id: "old", state: "answered" })]);
    expect(getCalls().map((c) => [c.call_id, c.state])).toEqual([
      ["old", "answered"],
      ["live", "ringing"],
    ]);
  });
});
