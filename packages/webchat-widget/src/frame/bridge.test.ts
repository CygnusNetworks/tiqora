import { describe, expect, it, vi } from "vitest";
import { createFrameBridge } from "./bridge";

const open = { p: "tiqora-chat/1", type: "open" };

function fixture(hostOrigin = "https://host.example") {
  const parent = { postMessage: vi.fn() };
  const listeners: Array<(e: MessageEvent) => void> = [];
  const win = {
    parent,
    addEventListener: (_: string, f: (e: MessageEvent) => void) => listeners.push(f),
    removeEventListener: (_: string, f: (e: MessageEvent) => void) => listeners.splice(listeners.indexOf(f), 1),
  } as unknown as Window;
  const onHostMessage = vi.fn();
  const bridge = createFrameBridge({ hostOrigin, onHostMessage, win });
  const dispatch = (o: { origin: string; source: unknown; data: unknown }) => listeners.forEach((f) => f(o as MessageEvent));
  return { parent, onHostMessage, bridge, dispatch, listeners };
}

describe("frame bridge", () => {
  it("ignores foreign origins, foreign sources and bad shapes", () => {
    const f = fixture();
    f.dispatch({ origin: "https://evil.example", source: f.parent, data: open });
    f.dispatch({ origin: "https://host.example", source: {}, data: open });
    f.dispatch({ origin: "https://host.example", source: f.parent, data: { ...open, extra: 1 } });
    f.dispatch({ origin: "https://host.example", source: f.parent, data: { p: "tiqora-chat/1", type: "set-topic", topic: "<x>" } });
    expect(f.onHostMessage).not.toHaveBeenCalled();
    f.dispatch({ origin: "https://host.example", source: f.parent, data: open });
    expect(f.onHostMessage).toHaveBeenCalledTimes(1);
  });

  it("accepts identify and logout from the host, drops a malformed token", () => {
    const f = fixture();
    const from = (data: unknown) => f.dispatch({ origin: "https://host.example", source: f.parent, data });
    from({ p: "tiqora-chat/1", type: "identify", token: "a.b.c" });
    from({ p: "tiqora-chat/1", type: "logout" });
    from({ p: "tiqora-chat/1", type: "identify", token: "not a token" });
    from({ p: "tiqora-chat/1", type: "identify" });
    expect(f.onHostMessage.mock.calls.map((c) => c[0])).toEqual([
      { p: "tiqora-chat/1", type: "identify", token: "a.b.c" },
      { p: "tiqora-chat/1", type: "logout" },
    ]);
  });

  it("posts identity and bot message notices to the host origin", () => {
    const f = fixture();
    f.bridge.post({ p: "tiqora-chat/1", type: "identity", verified: true });
    f.bridge.post({ p: "tiqora-chat/1", type: "message", sender: "bot" });
    expect(f.parent.postMessage).toHaveBeenCalledWith({ p: "tiqora-chat/1", type: "identity", verified: true }, "https://host.example");
    expect(f.parent.postMessage).toHaveBeenCalledWith({ p: "tiqora-chat/1", type: "message", sender: "bot" }, "https://host.example");
  });

  it("posts only to the host origin, never *", () => {
    const f = fixture();
    f.bridge.post({ p: "tiqora-chat/1", type: "close-request" });
    expect(f.parent.postMessage).toHaveBeenCalledWith({ p: "tiqora-chat/1", type: "close-request" }, "https://host.example");
    const star = fixture("*");
    star.bridge.post({ p: "tiqora-chat/1", type: "close-request" });
    star.dispatch({ origin: "*", source: star.parent, data: open });
    expect(star.parent.postMessage).not.toHaveBeenCalled();
    expect(star.onHostMessage).not.toHaveBeenCalled();
  });

  it("dispose removes the listener", () => {
    const f = fixture();
    f.bridge.dispose();
    expect(f.listeners).toHaveLength(0);
  });
});
