import { describe, it, expect } from "vitest";
import {
  dateRangeForPreset,
  formatYmd,
  isPresetActive,
  type DateRangePreset,
} from "./dateRanges";

/** Fixed reference: Wednesday 2026-03-18 local noon (avoids DST edge ambiguity). */
const REF = new Date(2026, 2, 18, 12, 0, 0); // March 18, 2026

describe("formatYmd", () => {
  it("pads month and day", () => {
    expect(formatYmd(new Date(2026, 0, 5))).toBe("2026-01-05");
    expect(formatYmd(new Date(2026, 11, 31))).toBe("2026-12-31");
  });
});

describe("dateRangeForPreset", () => {
  it("today and yesterday", () => {
    expect(dateRangeForPreset("today", REF)).toEqual({
      from: "2026-03-18",
      to: "2026-03-18",
    });
    expect(dateRangeForPreset("yesterday", REF)).toEqual({
      from: "2026-03-17",
      to: "2026-03-17",
    });
  });

  it("thisWeek is Mon–today (ISO week)", () => {
    // 2026-03-18 is Wednesday → week starts Mon 2026-03-16
    expect(dateRangeForPreset("thisWeek", REF)).toEqual({
      from: "2026-03-16",
      to: "2026-03-18",
    });
  });

  it("last7Days and last30Days are inclusive rolling windows ending today", () => {
    expect(dateRangeForPreset("last7Days", REF)).toEqual({
      from: "2026-03-12",
      to: "2026-03-18",
    });
    expect(dateRangeForPreset("last30Days", REF)).toEqual({
      from: "2026-02-17",
      to: "2026-03-18",
    });
  });

  it("thisMonth is first of month through today", () => {
    expect(dateRangeForPreset("thisMonth", REF)).toEqual({
      from: "2026-03-01",
      to: "2026-03-18",
    });
  });

  it("lastMonth is the full previous calendar month (handles year boundary)", () => {
    expect(dateRangeForPreset("lastMonth", REF)).toEqual({
      from: "2026-02-01",
      to: "2026-02-28",
    });
    // January → previous December of prior year
    const jan5 = new Date(2026, 0, 5, 12, 0, 0);
    expect(dateRangeForPreset("lastMonth", jan5)).toEqual({
      from: "2025-12-01",
      to: "2025-12-31",
    });
  });

  it("thisQuarter is quarter start through today", () => {
    // March is Q1 (Jan–Mar)
    expect(dateRangeForPreset("thisQuarter", REF)).toEqual({
      from: "2026-01-01",
      to: "2026-03-18",
    });
    // April is Q2
    const apr = new Date(2026, 3, 10, 12, 0, 0);
    expect(dateRangeForPreset("thisQuarter", apr)).toEqual({
      from: "2026-04-01",
      to: "2026-04-10",
    });
    // July is Q3
    const jul = new Date(2026, 6, 1, 12, 0, 0);
    expect(dateRangeForPreset("thisQuarter", jul)).toEqual({
      from: "2026-07-01",
      to: "2026-07-01",
    });
    // October is Q4
    const oct = new Date(2026, 9, 15, 12, 0, 0);
    expect(dateRangeForPreset("thisQuarter", oct)).toEqual({
      from: "2026-10-01",
      to: "2026-10-15",
    });
  });

  it("lastQuarter is the full previous 3-month block (handles year boundary)", () => {
    // In Q1 → last quarter is Q4 of previous year
    expect(dateRangeForPreset("lastQuarter", REF)).toEqual({
      from: "2025-10-01",
      to: "2025-12-31",
    });
    // In Q2 → last is Q1
    const may = new Date(2026, 4, 20, 12, 0, 0);
    expect(dateRangeForPreset("lastQuarter", may)).toEqual({
      from: "2026-01-01",
      to: "2026-03-31",
    });
    // In Q3 → last is Q2
    const aug = new Date(2026, 7, 1, 12, 0, 0);
    expect(dateRangeForPreset("lastQuarter", aug)).toEqual({
      from: "2026-04-01",
      to: "2026-06-30",
    });
  });

  it("thisYear is Jan 1 through today", () => {
    expect(dateRangeForPreset("thisYear", REF)).toEqual({
      from: "2026-01-01",
      to: "2026-03-18",
    });
  });

  it("lastYear is the full previous calendar year", () => {
    expect(dateRangeForPreset("lastYear", REF)).toEqual({
      from: "2025-01-01",
      to: "2025-12-31",
    });
  });

  it("reset clears both ends (all-time)", () => {
    expect(dateRangeForPreset("reset", REF)).toEqual({ from: "", to: "" });
  });

  it("last day of month uses day-0 arithmetic (31-day months)", () => {
    // On April 1, last month is March 1–31
    const apr1 = new Date(2026, 3, 1, 12, 0, 0);
    expect(dateRangeForPreset("lastMonth", apr1)).toEqual({
      from: "2026-03-01",
      to: "2026-03-31",
    });
  });
});

describe("isPresetActive", () => {
  it("matches when both ends equal the preset range", () => {
    const range = dateRangeForPreset("thisMonth", REF);
    expect(isPresetActive("thisMonth", range.from, range.to, REF)).toBe(true);
    expect(isPresetActive("thisMonth", range.from, "2099-01-01", REF)).toBe(false);
  });

  it("reset is active only when both are empty", () => {
    expect(isPresetActive("reset", "", "", REF)).toBe(true);
    expect(isPresetActive("reset", "2026-01-01", "", REF)).toBe(false);
  });

  it("manual edits deactivate every non-matching preset", () => {
    const presets: DateRangePreset[] = [
      "today",
      "yesterday",
      "thisWeek",
      "last7Days",
      "thisMonth",
      "lastMonth",
      "thisQuarter",
      "lastQuarter",
      "thisYear",
      "lastYear",
      "last30Days",
      "reset",
    ];
    for (const p of presets) {
      expect(isPresetActive(p, "2000-01-01", "2000-01-02", REF)).toBe(false);
    }
  });
});
