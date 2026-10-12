import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_ITSM_MODE_KEY, demoItsmMode } from "./itsmMode";

describe("demoItsmMode", () => {
  afterEach(() => {
    localStorage.removeItem(DEMO_ITSM_MODE_KEY);
    vi.restoreAllMocks();
  });

  it("defaults to the Tiqora CMDB (Mode B)", () => {
    expect(demoItsmMode()).toBe("tiqora");
  });

  it("reads a stored mode", () => {
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "znuny_readthrough");
    expect(demoItsmMode()).toBe("znuny_readthrough");
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "off");
    expect(demoItsmMode()).toBe("off");
  });

  it("ignores unknown values", () => {
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "mode-a");
    expect(demoItsmMode()).toBe("tiqora");
  });

  it("falls back when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(demoItsmMode()).toBe("tiqora");
  });
});
