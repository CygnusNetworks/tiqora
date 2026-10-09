import { describe, it, expect } from "vitest";
import {
  assignSeriesSlots,
  displaySlots,
  seriesColor,
  sequentialColor,
  keyColor,
  valueTextColor,
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
  it("__other__ never takes a slot and is always neutral", () => {
    const a = assignSeriesSlots(["7", "__other__", "3"]);
    expect(a.has("__other__")).toBe(false);
    expect([a.get("3"), a.get("7")]).toEqual([0, 1]);
    expect(keyColor("__other__", a)).toBe("var(--color-muted)");
    expect(keyColor("3", a)).toBe("var(--color-series-1)");
    // with a fixed order, __other__ still does not shift the slots
    const b = assignSeriesSlots(["__other__", "phone"], ["email", "phone"]);
    expect(b.get("phone")).toBe(1);
    expect(keyColor("__other__", b)).toBe("var(--color-muted)");
  });
  it("slots are stable across repeated calls and input order", () => {
    const one = assignSeriesSlots(["9", "2", "5"]);
    const two = assignSeriesSlots(["5", "9", "2"]);
    expect([...one.entries()].sort()).toEqual([...two.entries()].sort());
  });
  it("heatmap value text is dark on light steps and white on dark steps", () => {
    expect(valueTextColor(0)).toBe("#0d1b2e");
    expect(valueTextColor(0.5)).toBe("#0d1b2e");
    expect(valueTextColor(0.6)).toBe("#ffffff");
    expect(valueTextColor(1)).toBe("#ffffff");
  });

  it("gives a late fixed-order key a free colour while at most six series show", () => {
    // "internal" is 7th in the channel order: it takes the highest free slot, so it keeps that colour
    // whichever of the early channels a filter leaves over.
    const two = displaySlots(["email", "internal"], CHANNEL_ORDER);
    expect(two.get("email")).toBe(0);
    expect(two.get("internal")).toBe(5);
    const some = displaySlots(["phone", "internal", "webchat"], CHANNEL_ORDER);
    expect(some.get("phone")).toBe(1);
    expect(some.get("webchat")).toBe(3);
    expect(some.get("internal")).toBe(5);
    // the slot of an early key is never taken: with whatsapp present, internal moves to the next free one
    const withWhatsapp = displaySlots(["email", "whatsapp", "internal"], CHANNEL_ORDER);
    expect(withWhatsapp.get("whatsapp")).toBe(5);
    expect(withWhatsapp.get("internal")).toBe(4);
    // seven series: the stable order applies and the 7th folds (slot >= SERIES_SLOTS)
    const all = displaySlots([...CHANNEL_ORDER], CHANNEL_ORDER);
    expect(all.get("internal")).toBe(6);
    expect(all.get("internal")! >= SERIES_SLOTS).toBe(true);
  });
});
