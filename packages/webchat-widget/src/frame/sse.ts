/**
 * Fetch-based SSE client. `EventSource` cannot send the `X-Chat-Token` header and a
 * token in the URL would end up in proxy logs, so the stream is read with
 * `fetch` + `ReadableStream` (C7).
 */

export type SseEvent = { id: string | null; event: string; data: string };

/** Incremental SSE parser: feed chunks, get the events completed by each chunk. */
export function createSseParser(): (chunk: string) => SseEvent[] {
  let buf = "";
  let id: string | null = null;
  let event = "";
  let data: string[] = [];

  return (chunk) => {
    buf += chunk;
    const out: SseEvent[] = [];
    // A trailing "\r" may be the first half of "\r\n": keep it for the next chunk.
    const held = buf.endsWith("\r") ? "\r" : "";
    const lines = (held ? buf.slice(0, -1) : buf).split(/\r\n|\n|\r/);
    buf = (lines.pop() ?? "") + held;
    for (const line of lines) {
      if (line === "") {
        if (data.length) out.push({ id, event: event || "message", data: data.join("\n") });
        id = null;
        event = "";
        data = [];
        continue;
      }
      if (line.startsWith(":")) continue;
      const i = line.indexOf(":");
      const field = i < 0 ? line : line.slice(0, i);
      let value = i < 0 ? "" : line.slice(i + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "data") data.push(value);
      else if (field === "event") event = value;
      else if (field === "id" && !value.includes("\0")) id = value;
    }
    return out;
  };
}

/**
 * Reason of an `end` event. Unknown reasons (the server may add some, e.g. "ended") are
 * a generic end, never an error.
 */
export function endReason(data: string): string {
  try {
    const r = (JSON.parse(data) as { reason?: unknown } | null)?.reason;
    return typeof r === "string" && r ? r : "ended";
  } catch {
    return "ended";
  }
}

export type StreamState = "connecting" | "live" | "reconnecting" | "closed";

export type ChatStreamOptions = {
  url: string;
  token: string;
  lastSeq: () => number;
  onEvent: (e: SseEvent) => void;
  onState: (s: StreamState) => void;
  fetchImpl?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
  random?: () => number;
};

const BACKOFF_MS = [1000, 2000, 5000, 10000, 30000];
const MAX_RETRY_AFTER_MS = 120_000;
const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export class ChatStream {
  private ctl: AbortController | null = null;
  private stopped = true;
  private state: StreamState | null = null;
  private attempt = 0;

  constructor(private readonly o: ChatStreamOptions) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.attempt = 0;
    this.setState("connecting");
    void this.run();
  }

  stop(): void {
    this.stopped = true;
    this.ctl?.abort();
    this.setState("closed");
  }

  private setState(s: StreamState): void {
    if (this.state === s) return;
    this.state = s;
    this.o.onState(s);
  }

  private close(): void {
    this.stopped = true;
    this.ctl?.abort();
    this.setState("closed");
  }

  private async run(): Promise<void> {
    const doFetch = this.o.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
    const sleep = this.o.sleep ?? defaultSleep;
    const random = this.o.random ?? Math.random;
    while (!this.stopped) {
      let wait: number | null = null;
      const ctl = new AbortController();
      this.ctl = ctl;
      try {
        const res = await doFetch(this.o.url, {
          headers: {
            "X-Chat-Token": this.o.token,
            Accept: "text/event-stream",
            "Last-Event-ID": String(this.o.lastSeq()),
          },
          cache: "no-store",
          signal: ctl.signal,
        });
        if (this.stopped) return;
        if (res.status === 404 || res.status === 403) return this.close();
        if (res.status === 429 || res.status === 503) {
          const s = Number(res.headers.get("Retry-After"));
          wait = Math.min(Number.isFinite(s) && s > 0 ? s * 1000 : 30_000, MAX_RETRY_AFTER_MS);
        } else if (!res.ok || !res.body) {
          if (res.status >= 400 && res.status < 500 && res.status !== 408) return this.close();
        } else {
          this.setState("live");
          if (await this.read(res.body.getReader())) return;
        }
      } catch {
        if (this.stopped) return;
      }
      if (this.stopped) return;
      this.setState("reconnecting");
      if (wait === null) {
        const base = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)];
        this.attempt++;
        wait = Math.round(base * (1 + 0.2 * random()));
      }
      await sleep(wait);
    }
  }

  /** Pumps the body; resolves true when the stream ended for good (`end` event). */
  private async read(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<boolean> {
    const parse = createSseParser();
    const dec = new TextDecoder("utf-8");
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return false;
      for (const e of parse(dec.decode(value, { stream: true }))) {
        this.attempt = 0;
        try {
          this.o.onEvent(e);
        } catch {
          // a throwing handler must not kill the stream
        }
        if (e.event === "end") {
          this.close();
          return true;
        }
      }
    }
  }
}
