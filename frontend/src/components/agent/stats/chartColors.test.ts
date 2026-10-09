import { describe, it, expect } from "vitest";
import {
  assignSeriesSlots,
  seriesColor,
  sequentialColor,
  CHANNEL_ORDER,
  SERIES_SLOTS,
} from "./chartColors";

describe("chartColors", () => {
  it("orders id-valued series by key, not by value rank", () => {
    const a = assignSeriesSlots(["7", "3", "12"]);
    expect([a.get("3"), a.get("7"), a.get("12")]).toEqual([0, 1, 2]);
  });
  it("fixed enum order wins", () => {
    expect(assignSeriesSlots(["telegram", "email"], ["email", "phone", "telegram"]).get("telegram")).toBe(2);
    expect(assignSeriesSlots(["telegram", "email"], CHANNEL_ORDER).get("email")).toBe(0);
  });
  it("keys outside the fixed order come after it", () => {
    const a = assignSeriesSlots(["x", "email"], ["email", "phone"]);
    expect(a.get("email")).toBe(0);
    expect(a.get("x")).toBe(2);
  });
  it("slots beyond six fall back to muted", () => {
    expect(SERIES_SLOTS).toBe(6);
    expect(seriesColor(0)).toBe("var(--color-series-1)");
    expect(seriesColor(5)).toBe("var(--color-series-6)");
    expect(seriesColor(6)).toBe("var(--color-muted)");
  });
  it("maps 0..1 onto the seven sequential steps", () => {
    expect(sequentialColor(0)).toBe("var(--color-seq-1)");
    expect(sequentialColor(0.5)).toBe("var(--color-seq-4)");
    expect(sequentialColor(1)).toBe("var(--color-seq-7)");
    expect(sequentialColor(7)).toBe("var(--color-seq-7)");
    expect(sequentialColor(Number.NaN)).toBe("var(--color-seq-1)");
  });
});
