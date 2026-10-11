import { describe, expect, it } from "vitest";
import { SharedChatStream } from "./shared-stream";
import type { SseEvent, StreamState } from "./sse";

// In-memory Web Locks + BroadcastChannel shared by several fake "tabs".
function world() {
  const holders = new Map<string, { release: () => void } | null>();
  const waiting = new Map<string, Array<() => void>>();
  const channels = new Map<string, Set<{ onmessage: ((e: { data: unknown }) => void) | null }>>();
  const grant = (name: string) => {
    const next = waiting.get(name)?.shift();
    if (next) next();
  };
  return {
    locks: {
      request(name: string, o: { signal?: AbortSignal }, cb: () => Promise<void>) {
        return new Promise<void>((resolve, reject) => {
          const run = async () => {
            holders.set(name, { release: () => undefined });
            try {
              await cb();
            } finally {
              holders.set(name, null);
              grant(name);
              resolve();
            }
          };
          if (!holders.get(name)) void run();
          else {
            const w = () => void run();
            waiting.set(name, [...(waiting.get(name) ?? []), w]);
            o.signal?.addEventListener("abort", () => {
              waiting.set(name, (waiting.get(name) ?? []).filter((x) => x !== w));
              reject(new Error("aborted"));
            });
          }
        });
      },
    },
    openChannel(name: string) {
      const me = { onmessage: null as ((e: { data: unknown }) => void) | null };
      const set = channels.get(name) ?? new Set();
      set.add(me);
      channels.set(name, set);
      return {
        get onmessage() {
          return me.onmessage;
        },
        set onmessage(f) {
          me.onmessage = f;
        },
        postMessage(m: unknown) {
          for (const c of set) if (c !== me) c.onmessage?.({ data: structuredClone(m) });
        },
        close() {
          set.delete(me);
        },
      };
    },
  };
}

function tab(w: ReturnType<typeof world>, fetchCalls: string[], name: string, res: () => Response) {
  const events: SseEvent[] = [];
  const states: StreamState[] = [];
  const s = new SharedChatStream({
    sessionId: "sid",
    url: "/x",
    token: "t",
    lastSeq: () => 0,
    onEvent: (e) => events.push(e),
    onState: (st) => states.push(st),
    fetchImpl: (async () => {
      fetchCalls.push(name);
      return res();
    }) as typeof fetch,
    sleep: () => new Promise<void>(() => undefined),
    locks: w.locks,
    openChannel: (n) => w.openChannel(n),
  });
  return { s, events, states };
}
const sse = (text: string, hold = true) => {
  const enc = new TextEncoder();
  let sent = false;
  return new Response(
    new ReadableStream<Uint8Array>({
      pull(c) {
        if (!sent) {
          sent = true;
          c.enqueue(enc.encode(text));
        } else if (!hold) c.close();
        else return new Promise(() => undefined);
      },
    }),
  );
};
const tick = () => new Promise((r) => setTimeout(r, 5));

describe("SharedChatStream", () => {
  it("opens one fetch for two tabs and relays events to the follower", async () => {
    const w = world();
    const calls: string[] = [];
    const a = tab(w, calls, "a", () => sse('id: 1\nevent: message\ndata: {"seq":1}\n\n'));
    const b = tab(w, calls, "b", () => sse(""));
    a.s.start();
    b.s.start();
    await tick();
    expect(calls).toEqual(["a"]);
    expect(a.events).toHaveLength(1);
    expect(b.events).toEqual(a.events);
    expect(b.states).toContain("live");
    a.s.stop();
    b.s.stop();
  });

  it("hands over to the follower when the leader tab goes away", async () => {
    const w = world();
    const calls: string[] = [];
    const a = tab(w, calls, "a", () => sse(""));
    const b = tab(w, calls, "b", () => sse(""));
    a.s.start();
    b.s.start();
    await tick();
    a.s.stop();
    await tick();
    expect(calls).toEqual(["a", "b"]);
    b.s.stop();
  });

  it("closes followers when the chat ended", async () => {
    const w = world();
    const calls: string[] = [];
    const a = tab(w, calls, "a", () => sse('event: end\ndata: {"reason":"ended"}\n\n'));
    const b = tab(w, calls, "b", () => sse(""));
    a.s.start();
    b.s.start();
    await tick();
    expect(b.states.at(-1)).toBe("closed");
    expect(calls).toEqual(["a"]);
  });

  it("falls back to a direct stream without Web Locks", async () => {
    const calls: string[] = [];
    const s = new SharedChatStream({
      sessionId: "sid",
      url: "/x",
      token: "t",
      lastSeq: () => 0,
      onEvent: () => undefined,
      onState: () => undefined,
      fetchImpl: (async () => {
        calls.push("x");
        return sse("");
      }) as typeof fetch,
      locks: null,
    });
    s.start();
    await tick();
    expect(calls).toEqual(["x"]);
    s.stop();
  });
});
