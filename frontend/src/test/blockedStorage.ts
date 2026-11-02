import { vi } from "vitest";

type Method = "getItem" | "setItem" | "removeItem";

/**
 * Replaces localStorage with one whose given methods throw; the others reach the real storage.
 * (Spying on the instance does not take on every Node: jsdom's Storage stores an assigned
 * `setItem` as an item and keeps its own method.) Undo with vi.unstubAllGlobals().
 */
export function blockLocalStorage(...blocked: Method[]): void {
  const real = window.localStorage;
  const guard = (m: Method) => {
    if (blocked.includes(m)) throw new DOMException(`${m} blocked`, "SecurityError");
  };
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => {
      guard("getItem");
      return real.getItem(k);
    },
    setItem: (k: string, v: string) => {
      guard("setItem");
      real.setItem(k, v);
    },
    removeItem: (k: string) => {
      guard("removeItem");
      real.removeItem(k);
    },
    clear: () => real.clear(),
    key: (i: number) => real.key(i),
    get length() {
      return real.length;
    },
  });
}
