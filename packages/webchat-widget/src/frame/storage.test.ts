import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearSession, loadSession, saveSession, safeStorage, visitorId } from "./storage";

// Node >= 22 ships a global `localStorage` stub that shadows jsdom's: install a Map-backed one.
function installStorage() {
  const m = new Map<string, string>();
  const fake = {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, String(v)),
    removeItem: (k: string) => void m.delete(k),
    clear: () => m.clear(),
  };
  Object.defineProperty(window, "localStorage", { value: fake, configurable: true, writable: true });
  return fake;
}
let fake = installStorage();
beforeEach(() => {
  fake = installStorage();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("storage", () => {
  it("falls back to memory when localStorage throws", () => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new DOMException("blocked", "SecurityError");
      },
    });
    const s = { sessionId: "abc", token: "tok", lastSeq: 3 };
    saveSession("k", s);
    expect(loadSession("k")).toEqual(s);
    const id = visitorId();
    expect(visitorId()).toBe(id);
    clearSession("k");
    expect(loadSession("k")).toBeNull();
  });

  it("falls back when setItem throws (quota)", () => {
    vi.spyOn(fake, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    const st = safeStorage();
    st.set("a", "1");
    expect(st.get("a")).toBe("1");
    st.remove("a");
    expect(st.get("a")).toBeNull();
  });

  it("validates the stored shape", () => {
    const st = safeStorage();
    st.set("tiqora-chat:session:k", JSON.stringify({ sessionId: 5 }));
    expect(loadSession("k")).toBeNull();
    st.set("tiqora-chat:session:k", "{nope");
    expect(loadSession("k")).toBeNull();
    st.set("tiqora-chat:session:k", JSON.stringify({ sessionId: "a", token: "t", lastSeq: -1 }));
    expect(loadSession("k")).toBeNull();
    saveSession("k", { sessionId: "a", token: "t", lastSeq: 0 });
    expect(loadSession("k")).toEqual({ sessionId: "a", token: "t", lastSeq: 0 });
  });

  it("creates a 22 char base64url visitor id once", () => {
    const id = visitorId();
    expect(id).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(visitorId()).toBe(id);
    window.localStorage.setItem("tiqora-chat:visitor", "bad id!");
    expect(visitorId()).toMatch(/^[A-Za-z0-9_-]{22}$/);
  });
});
