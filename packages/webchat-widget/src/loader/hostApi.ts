/**
 * `window.Tiqora.chat`, the host page's API (C9). The page may be hostile or buggy: every
 * argument is re-checked by the caller, callbacks run in try/catch, and the details handed
 * out carry no message text and no session data, only `{online}` / `{sender}` style facts.
 */

export type HostEvent = "ready" | "open" | "close" | "message" | "availability";

export const HOST_EVENTS: readonly HostEvent[] = ["ready", "open", "close", "message", "availability"];

export type HostApiImpl = {
  open(): void;
  close(): void;
  toggle(): void;
  setTopic(key: string): void;
  on(event: HostEvent, cb: (detail: unknown) => void): () => void;
};

type Cb = (detail: unknown) => void;

/**
 * Event hub. `ready` and `availability` are sticky: a listener added after they fired is
 * called once (asynchronously) with the last detail, so a late `on("ready")` is not lost.
 */
export function createEvents(): { on: HostApiImpl["on"]; emit(event: HostEvent, detail?: unknown): void } {
  const subs = new Map<HostEvent, Set<Cb>>();
  const sticky = new Map<HostEvent, unknown>();
  const run = (cb: Cb, detail: unknown) => {
    try {
      cb(detail);
    } catch (e) {
      console.error("[tiqora-chat] listener failed", e);
    }
  };
  return {
    on(event, cb) {
      if (!HOST_EVENTS.includes(event) || typeof cb !== "function") return () => undefined;
      const set = subs.get(event) ?? new Set<Cb>();
      subs.set(event, set);
      set.add(cb);
      if (sticky.has(event)) {
        const detail = sticky.get(event);
        setTimeout(() => set.has(cb) && run(cb, detail), 0);
      }
      return () => void set.delete(cb);
    },
    emit(event, detail) {
      if (event === "ready" || event === "availability") sticky.set(event, detail);
      for (const cb of [...(subs.get(event) ?? [])]) run(cb, detail);
    },
  };
}

/**
 * Adds `chat` to `window.Tiqora` and touches no other key of it (the host or another
 * Tiqora widget may own the object). The exposed object holds exactly these five
 * functions, bound to nothing the page could reach.
 */
export function installHostApi(win: Window, impl: HostApiImpl): void {
  const chat = Object.freeze({
    open: () => impl.open(),
    close: () => impl.close(),
    toggle: () => impl.toggle(),
    setTopic: (key: string) => impl.setTopic(String(key)),
    on: (event: HostEvent, cb: Cb) => impl.on(event, cb),
  });
  const w = win as unknown as { Tiqora?: Record<string, unknown> };
  const existing = w.Tiqora;
  if (existing && typeof existing === "object") {
    try {
      existing.chat = chat;
      return;
    } catch {
      // frozen or exotic object: fall through and replace it by a copy
    }
    w.Tiqora = { ...existing, chat };
    return;
  }
  w.Tiqora = { chat };
}
