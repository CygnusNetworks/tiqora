import { describe, it, expect } from "vitest";
import { formatTimeUnits } from "./timeUnits";

describe("formatTimeUnits", () => {
  it("renders whole units without decimals", () => {
    expect(formatTimeUnits(15)).toBe("15");
    expect(formatTimeUnits(0)).toBe("0");
  });

  it("keeps a meaningful fraction", () => {
    expect(formatTimeUnits(7.5)).toBe("7.5");
    expect(formatTimeUnits(0.25)).toBe("0.25");
  });

  it("rounds to two decimals", () => {
    expect(formatTimeUnits(1.005)).toBe("1");
    expect(formatTimeUnits(2.128)).toBe("2.13");
  });

  it("falls back to zero for non-numbers", () => {
    expect(formatTimeUnits(Number.NaN)).toBe("0");
    expect(formatTimeUnits(Number.POSITIVE_INFINITY)).toBe("0");
  });
});
