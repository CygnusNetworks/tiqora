import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { receiveChatWaiting, resetChatRing, setRingContext } from "./chatRing";

let oscillators = 0;
const notifications: { title: string; opts: NotificationOptions | undefined }[] = [];

class FakeAudioContext {
  state = "running";
  currentTime = 0;
  destination = {};
  resume = () => Promise.resolve();
  createOscillator() {
    oscillators += 1;
    return { frequency: { value: 0 }, connect: vi.fn(), start: vi.fn(), stop: vi.fn() };
  }
  createGain() {
    return { gain: { value: 0 }, connect: vi.fn() };
  }
}

class FakeNotification {
  static permission = "granted";
  constructor(title: string, opts?: NotificationOptions) {
    notifications.push({ title, opts });
  }
}

/** One user gesture unlocks audio; each beep is two oscillators. */
function gesture() {
  window.dispatchEvent(new Event("pointerdown"));
}

describe("chat ring", () => {
  beforeEach(() => {
    oscillators = 0;
    notifications.length = 0;
    FakeNotification.permission = "granted";
    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("Notification", FakeNotification);
    window.AudioContext = FakeAudioContext as unknown as typeof AudioContext;
    resetChatRing();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("rings only when available and the topic matches, once per session", () => {
    setRingContext({ available: true, topics: ["1:billing"] });
    receiveChatWaiting({ session_id: "a", topic_ref: "1:support" });
    gesture();
    receiveChatWaiting({ session_id: "a", topic_ref: "1:support" });
    expect(oscillators).toBe(0);

    setRingContext({ available: true, topics: [] });
    receiveChatWaiting({ session_id: "b", topic_ref: "1:support" });
    expect(oscillators).toBe(2);
    receiveChatWaiting({ session_id: "b", topic_ref: "1:support" });
    expect(oscillators).toBe(2);

    setRingContext({ available: false, topics: [] });
    receiveChatWaiting({ session_id: "c", topic_ref: "1:support" });
    expect(oscillators).toBe(2);
  });

  it("stays silent before the first user gesture", () => {
    setRingContext({ available: true, topics: [] });
    receiveChatWaiting({ session_id: "a" });
    expect(oscillators).toBe(0);
  });

  it("notifies only with granted permission, tagged by session", () => {
    setRingContext({ available: true, topics: [], labels: { "1:support": "Support" } });
    receiveChatWaiting({ session_id: "a", topic_ref: "1:support" });
    expect(notifications).toHaveLength(1);
    expect(notifications[0].opts).toMatchObject({ tag: "a", body: "Support" });

    FakeNotification.permission = "default";
    receiveChatWaiting({ session_id: "b", topic_ref: "1:support" });
    expect(notifications).toHaveLength(1);
  });
});
