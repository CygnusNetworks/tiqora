import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { receiveChatWaiting, resetChatRing, setRingContext, useChatRing } from "./chatRing";
import { webchatApi } from "./webchatApi";

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

describe("useChatRing", () => {
  const wrapper = () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
  };

  afterEach(() => vi.restoreAllMocks());

  it("only fetches topics for an agent without chat topics", async () => {
    const topics = vi.spyOn(webchatApi, "topics").mockResolvedValue([]);
    const state = vi.spyOn(webchatApi, "getAgentState");
    const inbox = vi.spyOn(webchatApi, "inbox");
    const { result } = renderHook(() => useChatRing(), { wrapper: wrapper() });
    await waitFor(() => expect(topics).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(state).not.toHaveBeenCalled();
    expect(inbox).not.toHaveBeenCalled();
    expect(result.current.pending).toBe(0);
  });

  it("loads state and inbox once the agent has a topic", async () => {
    vi.spyOn(webchatApi, "topics").mockResolvedValue([
      { ref: "1:support", label: "Support", queue_id: 1, config_name: "Website" },
    ]);
    vi.spyOn(webchatApi, "getAgentState").mockResolvedValue({
      available: true, max_concurrent: 3, active_count: 0, topics: [],
    });
    vi.spyOn(webchatApi, "inbox").mockResolvedValue({ waiting: [], mine: [] });
    const { result } = renderHook(() => useChatRing(), { wrapper: wrapper() });
    await waitFor(() => expect(webchatApi.inbox).toHaveBeenCalled());
    expect(webchatApi.getAgentState).toHaveBeenCalled();
    expect(result.current.pending).toBe(0);
  });

  it("the badge counts only waiting chats: an assistant-led chat does not ring", async () => {
    const it = (sid: string, status: "bot" | "waiting") => ({
      session_id: sid, ticket_id: 1, tn: "1", title: "t", status, visitor_name: null,
      topic_label: null, waiting_since: null, accepted_at: null,
    });
    vi.spyOn(webchatApi, "topics").mockResolvedValue([
      { ref: "1:support", label: "Support", queue_id: 1, config_name: "Website" },
    ]);
    vi.spyOn(webchatApi, "getAgentState").mockResolvedValue({
      available: true, max_concurrent: 3, active_count: 0, topics: [],
    });
    vi.spyOn(webchatApi, "inbox").mockResolvedValue({
      waiting: [it("w1", "waiting")], bot: [it("b1", "bot"), it("b2", "bot")], mine: [],
    });
    const { result } = renderHook(() => useChatRing(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.pending).toBe(1);
  });
});
