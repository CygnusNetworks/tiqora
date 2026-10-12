/**
 * Visitor/session storage. Every access is guarded: private mode or blocked storage
 * makes `localStorage` throw, then an in-memory Map takes over for the page's life.
 */

const memory = new Map<string, string>();

export type Store = {
  get(k: string): string | null;
  set(k: string, v: string): void;
  remove(k: string): void;
};

export function safeStorage(): Store {
  return {
    get(k) {
      try {
        const v = window.localStorage.getItem(k);
        if (v !== null) return v;
      } catch {
        // fall through to memory
      }
      return memory.get(k) ?? null;
    },
    set(k, v) {
      try {
        window.localStorage.setItem(k, v);
        memory.delete(k);
        return;
      } catch {
        // quota / blocked
      }
      memory.set(k, v);
    },
    remove(k) {
      memory.delete(k);
      try {
        window.localStorage.removeItem(k);
      } catch {
        // blocked
      }
    },
  };
}

const VISITOR_KEY = "tiqora-chat:visitor";
const VISITOR_RE = /^[A-Za-z0-9_-]{16,64}$/;

function randomId(): string {
  const bytes = new Uint8Array(16);
  try {
    crypto.getRandomValues(bytes);
  } catch {
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Stable anonymous visitor id: 22 chars base64url (16 random bytes). */
export function visitorId(store: Store = safeStorage()): string {
  const have = store.get(VISITOR_KEY);
  if (have && VISITOR_RE.test(have)) return have;
  const id = randomId();
  store.set(VISITOR_KEY, id);
  return id;
}

/** `identified`: the chat belongs to a signed-in visitor; restored only after a fresh identify (C9). */
export type StoredSession = { sessionId: string; token: string; lastSeq: number; identified?: boolean };

const sessionKey = (key: string) => `tiqora-chat:session:${key}`;

export function loadSession(key: string, store: Store = safeStorage()): (StoredSession & { identified: boolean }) | null {
  const raw = store.get(sessionKey(key));
  if (!raw) return null;
  try {
    const o = JSON.parse(raw) as Record<string, unknown> | null;
    if (
      o &&
      typeof o.sessionId === "string" &&
      o.sessionId &&
      typeof o.token === "string" &&
      o.token &&
      typeof o.lastSeq === "number" &&
      Number.isInteger(o.lastSeq) &&
      o.lastSeq >= 0
    ) {
      return { sessionId: o.sessionId, token: o.token, lastSeq: o.lastSeq, identified: o.identified === true };
    }
  } catch {
    // corrupt value
  }
  return null;
}

export function saveSession(key: string, s: StoredSession, store: Store = safeStorage()): void {
  store.set(sessionKey(key), JSON.stringify({ sessionId: s.sessionId, token: s.token, lastSeq: s.lastSeq, identified: s.identified === true }));
}

export function clearSession(key: string, store: Store = safeStorage()): void {
  store.remove(sessionKey(key));
}
