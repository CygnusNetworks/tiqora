import { describe, it, expect } from "vitest";
import {
  TIME_RANGE_PRESETS,
  presetForRange,
  rangeForPreset,
  type TimeRangePreset,
} from "./dateRange";

/** Local noon avoids any DST/UTC edge that a midnight construction could hide. */
function at(y: number, m: number, d: number): Date {
  return new Date(y, m - 1, d, 12, 0, 0);
}

describe("rangeForPreset", () => {
  // Thursday, 6 August 2026 — Q3, mid-week, mid-month.
  const now = at(2026, 8, 6);

  const cases: [TimeRangePreset, string, string][] = [
    ["today", "2026-08-06", "2026-08-06"],
    ["yesterday", "2026-08-05", "2026-08-05"],
    ["thisWeek", "2026-08-03", "2026-08-06"],
    ["lastWeek", "2026-07-27", "2026-08-02"],
    ["thisMonth", "2026-08-01", "2026-08-06"],
    ["lastMonth", "2026-07-01", "2026-07-31"],
    ["thisQuarter", "2026-07-01", "2026-08-06"],
    ["lastQuarter", "2026-04-01", "2026-06-30"],
    ["thisYear", "2026-01-01", "2026-08-06"],
    ["lastYear", "2025-01-01", "2025-12-31"],
  ];

  it.each(cases)("%s → %s..%s", (preset, from, to) => {
    expect(rangeForPreset(preset, now)).toEqual({ from, to });
  });

  it("covers every exported preset", () => {
    expect(cases.map(([p]) => p)).toEqual([...TIME_RANGE_PRESETS]);
  });

  it("starts weeks on Monday, not Sunday", () => {
    // Sunday 15 March 2026 belongs to the week starting Monday 9 March.
    expect(rangeForPreset("thisWeek", at(2026, 3, 15))).toEqual({
      from: "2026-03-09",
      to: "2026-03-15",
    });
    // Monday 5 January 2026 is its own week start.
    expect(rangeForPreset("thisWeek", at(2026, 1, 5))).toEqual({
      from: "2026-01-05",
      to: "2026-01-05",
    });
  });
});

describe("rangeForPreset across period boundaries", () => {
  // 1 January 2026 (Thursday): every "last" period sits in the previous year.
  const newYear = at(2026, 1, 1);

  it("lastMonth is December of the previous year", () => {
    expect(rangeForPreset("lastMonth", newYear)).toEqual({
      from: "2025-12-01",
      to: "2025-12-31",
    });
  });

  it("lastQuarter is Q4 of the previous year", () => {
    expect(rangeForPreset("lastQuarter", newYear)).toEqual({
      from: "2025-10-01",
      to: "2025-12-31",
    });
  });

  it("thisWeek reaches back into the previous year", () => {
    expect(rangeForPreset("thisWeek", newYear)).toEqual({
      from: "2025-12-29",
      to: "2026-01-01",
    });
  });

  it("lastWeek spans a year boundary", () => {
    // Sunday 3 January 2027 → previous week is 21–27 December 2026.
    expect(rangeForPreset("lastWeek", at(2027, 1, 3))).toEqual({
      from: "2026-12-21",
      to: "2026-12-27",
    });
  });

  it("thisQuarter and thisYear collapse to a single day on 1 January", () => {
    expect(rangeForPreset("thisQuarter", newYear)).toEqual({
      from: "2026-01-01",
      to: "2026-01-01",
    });
    expect(rangeForPreset("thisYear", newYear)).toEqual({
      from: "2026-01-01",
      to: "2026-01-01",
    });
  });

  it("yesterday crosses into the previous year", () => {
    expect(rangeForPreset("yesterday", newYear)).toEqual({
      from: "2025-12-31",
      to: "2025-12-31",
    });
  });

  it("quarters are Jan–Mar, Apr–Jun, Jul–Sep, Oct–Dec", () => {
    expect(rangeForPreset("lastQuarter", at(2026, 4, 15))).toEqual({
      from: "2026-01-01",
      to: "2026-03-31",
    });
    expect(rangeForPreset("lastQuarter", at(2026, 7, 15))).toEqual({
      from: "2026-04-01",
      to: "2026-06-30",
    });
    expect(rangeForPreset("lastQuarter", at(2026, 10, 15))).toEqual({
      from: "2026-07-01",
      to: "2026-09-30",
    });
  });
});

describe("rangeForPreset in a leap year", () => {
  it("thisMonth ends on 29 February", () => {
    expect(rangeForPreset("thisMonth", at(2024, 2, 29))).toEqual({
      from: "2024-02-01",
      to: "2024-02-29",
    });
  });

  it("lastMonth is a 29-day February", () => {
    expect(rangeForPreset("lastMonth", at(2024, 3, 10))).toEqual({
      from: "2024-02-01",
      to: "2024-02-29",
    });
  });

  it("lastYear covers a full leap year", () => {
    expect(rangeForPreset("lastYear", at(2025, 5, 4))).toEqual({
      from: "2024-01-01",
      to: "2024-12-31",
    });
  });

  it("yesterday steps onto the leap day", () => {
    expect(rangeForPreset("yesterday", at(2024, 3, 1))).toEqual({
      from: "2024-02-29",
      to: "2024-02-29",
    });
  });
});

describe("presetForRange", () => {
  const now = at(2026, 8, 6);

  it("round-trips every preset", () => {
    for (const preset of TIME_RANGE_PRESETS) {
      const { from, to } = rangeForPreset(preset, now);
      expect(presetForRange(from, to, now)).toBe(preset);
    }
  });

  it("returns null for a hand-picked range", () => {
    expect(presetForRange("2026-05-03", "2026-05-17", now)).toBeNull();
  });

  it("returns null when only one bound is set", () => {
    expect(presetForRange("2026-08-06", undefined, now)).toBeNull();
    expect(presetForRange(undefined, "2026-08-06", now)).toBeNull();
    expect(presetForRange("", "", now)).toBeNull();
  });

  it("prefers the earliest preset when several collapse to the same span", () => {
    // On 1 January today/thisWeek?/thisMonth/thisQuarter/thisYear overlap.
    expect(presetForRange("2026-01-01", "2026-01-01", at(2026, 1, 1))).toBe("today");
  });
});
