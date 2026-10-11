import { describe, expect, it } from "vitest";
import { ChatStream, createSseParser, endReason, type SseEvent, type StreamState } from "./sse";

const enc = new TextEncoder();

function streamOf(chunks: string[], error?: Error): Response {
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(c) {
      if (i < chunks.length) c.enqueue(enc.encode(chunks[i++]));
      else if (error) c.error(error);
      else c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}
const never = () => new Promise<void>(() => undefined);
const tick = () => new Promise((r) => setTimeout(r, 0));
async function until(f: () => boolean) {
  for (let i = 0; i < 200 && !f(); i++) await tick();
  expect(f()).toBe(true);
}

function harness(responses: Array<Response | Error>, sleeps: number[] = []) {
  const states: StreamState[] = [];
  const events: SseEvent[] = [];
  const headers: Array<Record<string, string>> = [];
  let seq = 0;
  let n = 0;
  const stream = new ChatStream({
    url: "/api/chat/v1/sessions/s/stream",
    token: "tok",
    lastSeq: () => seq,
    onEvent: (e) => {
      events.push(e);
      if (e.event === "message") seq = Number(e.id);
    },
    onState: (s) => states.push(s),
    fetchImpl: (async (_u: unknown, init?: RequestInit) => {
      headers.push(init?.headers as Record<string, string>);
      const r = responses[Math.min(n++, responses.length - 1)];
      if (r instanceof Error) throw r;
      return r;
    }) as typeof fetch,
    sleep: async (ms) => {
      sleeps.push(ms);
      if (sleeps.length > 3) await never();
    },
    random: () => 0.5,
  });
  return { stream, states, events, headers, sleeps, calls: () => n };
}

describe("sse parser", () => {
  it("parses SSE across chunk boundaries", () => {
    const p = createSseParser();
    expect(p("id: 3\nevent: mess")).toEqual([]);
    expect(
      p('age\ndata: {"seq":3}\n\n: hb\n\nevent: typing\r\ndata: {"who":"agent"}\r\n\r\n'),
    ).toEqual([
      { id: "3", event: "message", data: '{"seq":3}' },
      { id: null, event: "typing", data: '{"who":"agent"}' },
    ]);
  });
  it("handles multi-line data, CR split from LF and default event", () => {
    const p = createSseParser();
    expect(p("data: a\r")).toEqual([]);
    expect(p("\ndata: b\r\n\r")).toEqual([]);
    expect(p("\n")).toEqual([{ id: null, event: "message", data: "a\nb" }]);
  });
  it("treats unknown end reasons as a generic end", () => {
    expect(endReason('{"reason":"agent"}')).toBe("agent");
    expect(endReason('{"reason":"ended"}')).toBe("ended");
    expect(endReason("garbage")).toBe("ended");
    expect(endReason('{"reason":5}')).toBe("ended");
  });
});

describe("ChatStream", () => {
  it("reconnects with Last-Event-ID after a dropped stream", async () => {
    const h = harness([
      streamOf(['id: 4\nevent: message\ndata: {"seq":4}\n\n'], new TypeError("network")),
      streamOf([]),
    ]);
    h.stream.start();
    await until(() => h.calls() >= 2);
    expect(h.headers[0]).toMatchObject({
      "X-Chat-Token": "tok",
      Accept: "text/event-stream",
      "Last-Event-ID": "0",
    });
    expect(h.headers[1]["Last-Event-ID"]).toBe("4");
    expect(h.sleeps[0]).toBeGreaterThanOrEqual(1000);
    expect(h.sleeps[0]).toBeLessThanOrEqual(1200);
    await until(() => h.states.filter((s) => s === "live").length >= 2);
    expect(h.states.slice(0, 4)).toEqual(["connecting", "live", "reconnecting", "live"]);
    h.stream.stop();
  });

  it("escalates backoff and resets after an event", async () => {
    const h = harness([new TypeError("x"), new TypeError("x"), new TypeError("x"), new TypeError("x")]);
    h.stream.start();
    await until(() => h.sleeps.length >= 4);
    h.stream.stop();
    expect(h.sleeps).toEqual([1100, 2200, 5500, 11000]);
  });

  it("honours Retry-After on 503 and stops on 404", async () => {
    const h = harness([
      new Response("{}", { status: 503, headers: { "Retry-After": "30" } }),
      new Response("{}", { status: 404 }),
    ]);
    h.stream.start();
    await until(() => h.states.includes("closed"));
    expect(h.sleeps).toEqual([30000]);
    expect(h.calls()).toBe(2);
    await tick();
    expect(h.calls()).toBe(2);
  });

  it("stops after an end event, also with an unknown reason", async () => {
    const h = harness([streamOf(['event: end\ndata: {"reason":"ended"}\n\n'])]);
    h.stream.start();
    await until(() => h.states.includes("closed"));
    expect(h.calls()).toBe(1);
    expect(h.events.map((e) => e.event)).toEqual(["end"]);
    expect(h.states).toEqual(["connecting", "live", "closed"]);
  });

  it("stop() aborts and does not reconnect", async () => {
    const h = harness([streamOf([])]);
    h.stream.start();
    h.stream.stop();
    await tick();
    expect(h.states.at(-1)).toBe("closed");
    expect(h.calls()).toBeLessThanOrEqual(1);
  });
});
