import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PLUGIN_SAFE_MODE_KEY, applySafeModeFromUrl, isPluginSafeMode } from "./safeMode";

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
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
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(isPluginSafeMode()).toBe(false);
  });

  it("?plugins=off still applies to this page load when storage refuses the write", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    applySafeModeFromUrl("?plugins=off");
    expect(isPluginSafeMode()).toBe(true);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    applySafeModeFromUrl("?plugins=on");
    expect(isPluginSafeMode()).toBe(false);
  });
});
