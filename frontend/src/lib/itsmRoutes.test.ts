import { describe, expect, it } from "vitest";
import { ciDetailHref } from "./itsmRoutes";

describe("ciDetailHref", () => {
  it("links Tiqora CIs to the CMDB page", () => {
    expect(ciDetailHref("tiqora", 42)).toBe("/agent/cmdb/42");
  });

  it("links Znuny CIs to the read-through page", () => {
    expect(ciDetailHref("znuny", 7)).toBe("/agent/itsm/cis/znuny/7");
  });
});
