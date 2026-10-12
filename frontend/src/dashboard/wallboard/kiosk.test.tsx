import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { linkTabIndex, rendersLinks, rowLinkMode } from "../chrome/mode";
import { KioskProvider, useIsKiosk } from "./kioskContext";

describe("kiosk context and link rules", () => {
  it("is false by default and true inside KioskProvider", () => {
    expect(renderHook(() => useIsKiosk()).result.current).toBe(false);
    expect(renderHook(() => useIsKiosk(), { wrapper: KioskProvider }).result.current).toBe(true);
  });

  it("rendersLinks(mode, kiosk): never in preview and never on a kiosk", () => {
    expect(rendersLinks("view")).toBe(true);
    expect(rendersLinks("wallboard")).toBe(true);
    expect(rendersLinks("edit")).toBe(true);
    expect(rendersLinks("preview")).toBe(false);
    expect(rendersLinks("wallboard", true)).toBe(false);
    expect(rendersLinks("view", true)).toBe(false);
  });

  it("row links follow", () => {
    expect(rowLinkMode("wallboard")).toBe("link");
    expect(rowLinkMode("wallboard", true)).toBe("none");
    expect(rowLinkMode("edit")).toBe("inert");
    expect(linkTabIndex("wallboard")).toBeUndefined();
  });
});
