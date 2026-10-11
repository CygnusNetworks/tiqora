import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PLUGIN_SAFE_MODE_KEY, applySafeModeFromUrl, isPluginSafeMode } from "./safeMode";

type Failing = Partial<Record<"getItem" | "setItem" | "removeItem", boolean>>;

/**
 * A sessionStorage stand-in whose methods can be made to throw. (Spying on
 * Storage.prototype or on the instance does not take effect on this Node's
 * native web storage, so the global is replaced instead.)
 */
function fakeStorage(fail: Failing = {}, initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  const guard = (name: keyof Failing) => {
    if (fail[name]) throw new DOMException(`${name} blocked`, "QuotaExceededError");
  };
  const storage = {
    getItem: (k: string) => {
      guard("getItem");
      return items.get(k) ?? null;
    },
    setItem: (k: string, v: string) => {
      guard("setItem");
      items.set(k, String(v));
    },
    removeItem: (k: string) => {
      guard("removeItem");
      items.delete(k);
    },
  };
  vi.stubGlobal("sessionStorage", storage);
  return items;
}

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  applySafeModeFromUrl("?plugins=on"); // working storage again: clears the in-memory flag
});

describe("browser safe mode", () => {
  it("?plugins=off switches plugins off for the session, ?plugins=on back on", () => {
    applySafeModeFromUrl("?plugins=off");
    expect(isPluginSafeMode()).toBe(true);
    expect(sessionStorage.getItem(PLUGIN_SAFE_MODE_KEY)).toBe("1");
    applySafeModeFromUrl("?foo=1");
    expect(isPluginSafeMode()).toBe(true);
    applySafeModeFromUrl("?plugins=on");
    expect(isPluginSafeMode()).toBe(false);
    expect(sessionStorage.getItem(PLUGIN_SAFE_MODE_KEY)).toBeNull();
  });

  it("blocked storage counts as not switched off", () => {
    fakeStorage({ getItem: true }, { [PLUGIN_SAFE_MODE_KEY]: "1" });
    expect(isPluginSafeMode()).toBe(false);
  });

  it("a failing write keeps ?plugins=off for this page load even though reads work", () => {
    const items = fakeStorage({ setItem: true });
    applySafeModeFromUrl("?plugins=off");
    expect(items.has(PLUGIN_SAFE_MODE_KEY)).toBe(false);
    expect(isPluginSafeMode()).toBe(true);
  });

  it("a failing removal keeps ?plugins=on for this page load although the stored flag says off", () => {
    const items = fakeStorage({ removeItem: true }, { [PLUGIN_SAFE_MODE_KEY]: "1" });
    applySafeModeFromUrl("?plugins=on");
    expect(items.get(PLUGIN_SAFE_MODE_KEY)).toBe("1");
    expect(isPluginSafeMode()).toBe(false);
  });

  it("?plugins=off still applies to this page load when storage is fully blocked", () => {
    fakeStorage({ getItem: true, setItem: true, removeItem: true });
    applySafeModeFromUrl("?plugins=off");
    expect(isPluginSafeMode()).toBe(true);
    applySafeModeFromUrl("?plugins=on");
    expect(isPluginSafeMode()).toBe(false);
  });
});
