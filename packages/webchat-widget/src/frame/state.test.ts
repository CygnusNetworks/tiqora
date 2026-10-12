import { describe, expect, it } from "vitest";
import { canStartNew, contiguousSeq, initial, isLive, maxSeq, reducer } from "./state";
import { cfg, msg } from "./testkit";

describe("reducer", () => {
  it("dedupes by seq and resolves pending by client_id", () => {
    let s = reducer(initial, { type: "pending", pending: { clientId: "c1", body: "Hallo", status: "sending" } });
    s = reducer(s, { type: "message", message: msg({ seq: 2, sender: "visitor", body: "Hallo", client_id: "c1" }) });
    s = reducer(s, { type: "message", message: msg({ seq: 2, sender: "visitor", body: "Hallo", client_id: "c1" }) });
    s = reducer(s, { type: "message", message: msg({ seq: 1, sender: "visitor", body: "Erste" }) });
    expect(s.messages.map((m) => m.seq)).toEqual([1, 2]);
    expect(s.pending).toEqual([]);
  });

  it("a restore racing relayed events neither duplicates nor reorders (M12)", () => {
    let s = reducer(initial, { type: "message", message: msg({ seq: 4 }) });
    s = reducer(s, { type: "message", message: msg({ seq: 3 }) });
    const session = { agent_name: null, can_handoff: false, can_send: true, last_seq: 5, needs_contact: false, session_id: "s", status: "active", topic_label: null };
    s = reducer(s, { type: "session-restored", session, messages: [msg({ seq: 1 }), msg({ seq: 2 }), msg({ seq: 3 }), msg({ seq: 4 })] });
    s = reducer(s, { type: "message", message: msg({ seq: 5 }) });
    expect(s.messages.map((m) => m.seq)).toEqual([1, 2, 3, 4, 5]);
    expect(maxSeq(s.messages)).toBe(5);
    expect(s.view).toBe("chat");
  });

  it("the resume cursor is the contiguous seq, not the max (F-I2)", () => {
    const m = (...seqs: number[]) => seqs.map((seq) => msg({ seq }));
    expect(contiguousSeq([])).toBe(0);
    expect(contiguousSeq(m(1, 2, 3))).toBe(3);
    expect(contiguousSeq(m(1, 2, 4, 5))).toBe(2); // seq 3 not seen yet
    expect(contiguousSeq(m(2, 3))).toBe(0);
    // a full read covered everything up to the floor (a deleted line leaves no hole there)
    expect(contiguousSeq(m(1, 3, 4, 6), 4)).toBe(4);
    expect(contiguousSeq(m(1, 3, 4, 5, 6), 4)).toBe(6);
  });

  it("a restart under offline_mode hide with nobody online keeps the ended chat (T16 M11)", () => {
    const session = { agent_name: null, can_handoff: false, can_send: false, last_seq: 2, needs_contact: false, session_id: "s", status: "ended", topic_label: null };
    let s = reducer(initial, { type: "session-restored", session, messages: [msg({ seq: 1 })] });
    s = reducer(s, { type: "config-loaded", config: cfg({ online: false, offline_mode: "hide" }) });
    expect(s.view).toBe("ended");
    expect(reducer(s, { type: "reset" })).toBe(s);
    s = reducer(s, { type: "config-loaded", config: cfg({ online: false, offline_mode: "form" }) });
    expect(reducer(s, { type: "reset" }).view).toBe("offline");
  });

  it("offline config shows the offline form or hides", () => {
    expect(reducer(initial, { type: "config-loaded", config: cfg({ online: false, offline_mode: "form" }) }).view).toBe("offline");
    expect(reducer(initial, { type: "config-loaded", config: cfg({ online: false, offline_mode: "hide" }) }).view).toBe("hidden");
    expect(reducer(initial, { type: "config-loaded", config: cfg() }).view).toBe("prechat");
    expect(reducer(initial, { type: "config-failed", status: 403 }).view).toBe("blocked");
    expect(reducer(initial, { type: "config-failed", status: 404 }).view).toBe("hidden");
  });

  it("counts unread only while closed, and not for duplicates", () => {
    let s = reducer(initial, { type: "message", message: msg({ seq: 1 }) });
    s = reducer(s, { type: "message", message: msg({ seq: 1 }) });
    s = reducer(s, { type: "message", message: msg({ seq: 2, sender: "visitor" }) });
    s = reducer(s, { type: "message", message: msg({ seq: 3, sender: "system" }) });
    expect(s.unread).toBe(2);
    s = reducer(s, { type: "opened" });
    expect(s.unread).toBe(0);
    s = reducer(s, { type: "message", message: msg({ seq: 4 }) });
    expect(s.unread).toBe(0);
  });

  it("ended and abandoned sessions end the chat; an unknown status stays out of live", () => {
    const session = { agent_name: null, can_handoff: false, can_send: true, last_seq: 0, needs_contact: false, session_id: "s", status: "active", topic_label: null };
    let s = reducer(initial, { type: "session-restored", session, messages: [] });
    s = reducer(s, { type: "session-event", status: "abandoned", agentName: null });
    expect(s.view).toBe("ended");
    expect(s.session?.can_send).toBe(false);
    expect(reducer(initial, { type: "session-restored", session: { ...session, status: "ended" }, messages: [] }).view).toBe("ended");
  });

  it("set-topic only accepts a topic of the loaded config", () => {
    let s = reducer(initial, { type: "config-loaded", config: cfg({ topics: [{ key: "sales", label: "Vertrieb", online: true, ai_first: false }] }) });
    expect(reducer(s, { type: "set-topic", topic: "nope" }).topic).toBeNull();
    s = reducer(s, { type: "set-topic", topic: "sales" });
    expect(s.topic).toBe("sales");
  });

  const botSession = { agent_name: null, bot_name: "Robo", can_handoff: true, can_send: true, last_seq: 1, needs_contact: false, session_id: "s", status: "bot", topic_label: null };

  it("bot is a live status; the handoff button state follows the status events", () => {
    expect(isLive("bot")).toBe(true);
    let s = reducer(initial, { type: "session-restored", session: botSession, messages: [] });
    expect(s.view).toBe("chat");
    s = reducer(s, { type: "handoff-started" });
    expect(s.handoffBusy).toBe(true);
    s = reducer(s, { type: "session-event", status: "waiting", agentName: null });
    expect(s.handoffBusy).toBe(false);
    expect(s.session).toMatchObject({ status: "waiting", can_handoff: false, bot_name: null, can_send: true });
    s = reducer(s, { type: "session-event", status: "bot", agentName: null, botName: "Robo" });
    expect(s.session).toMatchObject({ can_handoff: true, bot_name: "Robo" });
    s = reducer(s, { type: "handoff-started" });
    expect(reducer(s, { type: "handoff-failed" }).handoffBusy).toBe(false);
  });

  it("a created chat in status bot can hand off; typing tells who; a bot line ends the typing", () => {
    let s = reducer(initial, { type: "session-created", created: { session_id: "s", token: "t", status: "bot", message: msg({ seq: 1, sender: "visitor" }) } });
    expect(s.session?.can_handoff).toBe(true);
    s = reducer(s, { type: "typing", who: "bot" });
    expect([s.agentTyping, s.typingBot]).toEqual([true, true]);
    s = reducer(s, { type: "typing", who: "agent" });
    expect(s.typingBot).toBe(false);
    s = reducer(s, { type: "typing", who: "bot" });
    s = reducer(s, { type: "message", message: msg({ seq: 2, sender: "bot" }) });
    expect(s.agentTyping).toBe(false);
  });

  it("identity actions keep the sign-in across a restart and flag a refused one", () => {
    let s = reducer(initial, { type: "config-loaded", config: cfg() });
    s = reducer(s, { type: "identity-set", name: "Erika" });
    expect(s.identity).toEqual({ name: "Erika" });
    expect(reducer(s, { type: "reset" }).identity).toEqual({ name: "Erika" });
    s = reducer(s, { type: "identity-invalid" });
    expect([s.identity, s.identityError]).toEqual([null, true]);
    s = reducer(s, { type: "identity-set", name: null });
    expect(s.identityError).toBe(false);
    expect(reducer(s, { type: "identity-cleared" }).identity).toBeNull();
  });

  it("offline_mode ai starts a chat only when a topic is answered by the AI", () => {
    const topics = [{ key: "a", label: "A", online: false, ai_first: true }];
    expect(reducer(initial, { type: "config-loaded", config: cfg({ online: false, offline_mode: "ai", topics }) }).view).toBe("prechat");
    expect(reducer(initial, { type: "config-loaded", config: cfg({ online: false, offline_mode: "ai", topics: [{ ...topics[0], ai_first: false }] }) }).view).toBe("hidden");
    expect(canStartNew(cfg({ online: false, offline_mode: "ai", topics }))).toBe(true);
    expect(canStartNew(cfg({ online: false, offline_mode: "ai" }))).toBe(false);
  });
});
