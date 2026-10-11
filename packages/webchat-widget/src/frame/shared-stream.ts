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
  | { t: "state"; s: StreamState };

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

  constructor(private readonly o: SharedStreamOptions) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
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
    if (!locks || !open) {
      this.direct = new ChatStream(this.o);
      this.direct.start();
      return;
    }
    const ch = open(`tiqora-chat:stream:${this.o.sessionId}`);
    this.ch = ch;
    this.o.onState("connecting");
    ch.onmessage = (ev) => {
      const m = ev.data as Relay | null;
      if (this.inner || !m || typeof m !== "object") return; // the leader ignores relays
      if (m.t === "event") this.o.onEvent(m.e);
      else if (m.t === "state") {
        this.o.onState(m.s);
        if (m.s === "closed") this.stop(); // chat ended: nobody needs to take over
      }
    };
    const abort = new AbortController();
    this.abort = abort;
    void locks
      .request(`tiqora-chat:stream:${this.o.sessionId}`, { signal: abort.signal }, () => this.lead(ch))
      .catch(() => undefined); // aborted by stop()
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.direct?.stop();
    this.direct = null;
    this.inner?.stop();
    this.inner = null;
    this.abort?.abort();
    this.release?.();
    this.ch?.close();
    this.ch = null;
  }

  private lead(ch: ChannelLike): Promise<void> {
    if (this.stopped) return Promise.resolve();
    return new Promise<void>((resolve) => {
      this.release = resolve;
      const send = (m: Relay) => {
        try {
          ch.postMessage(m);
        } catch {
          // channel closed
        }
      };
      this.inner = new ChatStream({
        ...this.o,
        onEvent: (e) => {
          send({ t: "event", e });
          this.o.onEvent(e);
        },
        onState: (s) => {
          // A leader that is merely leaving (stop()) must not tell followers the chat is over.
          if (!this.stopped) send({ t: "state", s });
          this.o.onState(s);
          if (s === "closed") {
            this.stopped = true;
            this.inner = null;
            this.abort?.abort();
            this.ch?.close();
            this.ch = null;
            resolve();
          }
        },
      });
      this.inner.start();
    });
  }
}
