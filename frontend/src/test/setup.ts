import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// The 1 s default fails a slow render on a busy machine; see testTimeout in vitest.config.ts.
configure({ asyncUtilTimeout: 5_000 });

// jsdom doesn't implement scrollIntoView; components call it on focus
if (typeof Element.prototype.scrollIntoView !== "function") {
  Element.prototype.scrollIntoView = () => {};
}

// jsdom/Node may lack a full localStorage in some vitest environments
if (typeof globalThis.localStorage === "undefined" || !globalThis.localStorage) {
  const store = new Map<string, string>();
  globalThis.localStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      store.set(k, String(v));
    },
    removeItem: (k: string) => {
      store.delete(k);
    },
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size;
    },
  };
}
