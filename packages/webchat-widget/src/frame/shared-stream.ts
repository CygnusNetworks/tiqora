/**
 * One SSE stream per session per browser. The server evicts the oldest stream of a
 * session once there are more than 3 (R-SSE-CAPS), so several open tabs must not each
 * open one. Tabs elect a leader with the Web Locks API (the lock is released
 * automatically when the leader tab dies, and the next waiting tab takes over); the
 * leader runs the `ChatStream` and relays events and state over a BroadcastChannel.
 * Without Web Locks / BroadcastChannel (old browsers) every tab runs its own stream.
 */
import { ChatStream, type ChatStreamOptions, type SseEvent, type StreamState } from "./sse";

type Relay =
  | { t: "event"; e: SseEvent }
  | { t: "state"; s: StreamState }
  | { t: "hello" };

const STATES: readonly string[] = ["connecting", "live", "reconnecting", "closed"];

/** Shape guard for what arrives on the channel. */
function asRelay(v: unknown): Relay | null {
  if (!v || typeof v !== "object") return null;
  const m = v as { t?: unknown; e?: unknown; s?: unknown };
  if (m.t === "hello") return { t: "hello" };
  if (m.t === "state" && typeof m.s === "string" && STATES.includes(m.s)) {
    return { t: "state", s: m.s as StreamState };
  }
  if (m.t === "event" && m.e && typeof m.e === "object") {
    const e = m.e as { id?: unknown; event?: unknown; data?: unknown };
    if (
      (e.id === null || typeof e.id === "string") &&
      typeof e.event === "string" &&
      typeof e.data === "string"
    ) {
      return { t: "event", e: { id: e.id, event: e.event, data: e.data } };
    }
  }
  return null;
}

type LockManagerLike = {
  request(
    name: string,
    opts: { signal?: AbortSignal },
    cb: () => Promise<void>,
  ): Promise<unknown>;
};
type ChannelLike = {
  postMessage(m: unknown): void;
  close(): void;
  onmessage: ((ev: { data: unknown }) => void) | null;
};

export type SharedStreamOptions = ChatStreamOptions & {
  sessionId: string;
  locks?: LockManagerLike | null;
  openChannel?: ((name: string) => ChannelLike) | null;
};

export class SharedChatStream {
  private direct: ChatStream | null = null;
  private inner: ChatStream | null = null;
  private ch: ChannelLike | null = null;
  private abort: AbortController | null = null;
  private stopped = true;
  private release: (() => void) | null = null;
  private emitted: StreamState | null = null;
  /** Leader only: what a late joiner needs to catch up. */
  private lastSession: SseEvent | null = null;

  constructor(private readonly o: SharedStreamOptions) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.emitted = null;
    this.lastSession = null;
    const locks =
      this.o.locks === undefined
        ? ((globalThis.navigator?.locks as unknown as LockManagerLike | undefined) ?? null)
        : this.o.locks;
    const open =
      this.o.openChannel === undefined
        ? typeof BroadcastChannel === "function"
          ? (n: string) => new BroadcastChannel(n) as unknown as ChannelLike
          : null
        : this.o.openChannel;
    if (!locks || !open) return this.startDirect();
    let ch: ChannelLike;
    try {
      ch = open(`tiqora-chat:stream:${this.o.sessionId}`);
    } catch {
      return this.startDirect();
    }
    this.ch = ch;
    this.emit("connecting");
    ch.onmessage = (ev) => {
      const m = asRelay(ev.data);
      if (!m) return;
      if (this.inner) {
        // leader: answer a joining tab with the current state and the last session event
        if (m.t === "hello") {
          this.send({ t: "state", s: this.emitted ?? "connecting" });
          if (this.lastSession) this.send({ t: "event", e: this.lastSession });
        }
        return;
      }
      if (m.t === "event") this.o.onEvent(m.e);
      else if (m.t === "state") {
        this.emit(m.s);
        if (m.s === "closed") this.stop(); // chat ended: nobody needs to take over
      }
    };
    const abort = new AbortController();
    this.abort = abort;
    const refused = (err: unknown) => {
      if (this.stopped || (err as { name?: string } | null)?.name === "AbortError") return;
      this.startDirect(); // lock API present but refusing (SecurityError, blocked site data, ...)
    };
    try {
      locks
        .request(`tiqora-chat:stream:${this.o.sessionId}`, { signal: abort.signal }, () => this.lead())
        .catch(refused);
    } catch (err) {
      refused(err);
      return;
    }
    this.send({ t: "hello" });
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.direct?.stop();
    this.direct = null;
    this.inner?.stop();
    this.inner = null;
    this.teardownChannel();
  }

  private teardownChannel(): void {
    this.abort?.abort();
    this.abort = null;
    this.release?.();
    this.release = null;
    if (this.ch) this.ch.onmessage = null;
    this.ch?.close();
    this.ch = null;
  }

  /** Every tab runs its own stream (no Web Locks / BroadcastChannel, or they refuse). */
  private startDirect(): void {
    if (this.stopped || this.direct) return;
    this.teardownChannel();
    this.direct = new ChatStream({ ...this.o, onState: (s) => this.emit(s) });
    this.direct.start();
  }

  private emit(s: StreamState): void {
    if (this.emitted === s) return;
    this.emitted = s;
    this.o.onState(s);
  }

  private send(m: Relay): void {
    try {
      this.ch?.postMessage(m);
    } catch {
      // channel closed
    }
  }

  private lead(): Promise<void> {
    if (this.stopped) return Promise.resolve();
    return new Promise<void>((resolve) => {
      this.release = resolve;
      this.inner = new ChatStream({
        ...this.o,
        onEvent: (e) => {
          if (e.event === "session") this.lastSession = e;
          this.send({ t: "event", e });
          this.o.onEvent(e);
        },
        onState: (s) => {
          // A leader that is merely leaving (stop()) must not tell followers the chat is over.
          if (!this.stopped) {
            this.emitted = s;
            this.send({ t: "state", s });
            this.o.onState(s);
          } else this.emit(s);
          if (s === "closed" && !this.stopped) {
            this.stopped = true;
            this.inner = null;
            this.teardownChannel();
            resolve();
          }
        },
      });
      this.inner.start();
    });
  }
}
