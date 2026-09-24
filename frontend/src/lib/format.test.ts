import { describe, it, expect } from "vitest";
import { dayBucket, formatListTime } from "./format";

// Wednesday, 24 Sep 2026, 10:00 local time.
const NOW = new Date(2026, 8, 24, 10, 0);

describe("dayBucket", () => {
  it("groups by local calendar day, not by 24h distance", () => {
    expect(dayBucket(new Date(2026, 8, 24, 0, 5), NOW)).toBe("today");
    expect(dayBucket(new Date(2026, 8, 23, 23, 50), NOW)).toBe("yesterday");
    expect(dayBucket(new Date(2026, 8, 22, 11, 0), NOW)).toBe("week");
    expect(dayBucket(new Date(2026, 8, 18, 9, 0), NOW)).toBe("week");
    expect(dayBucket(new Date(2026, 8, 17, 9, 0), NOW)).toBe("older");
  });

  it("treats missing or unparsable values as older", () => {
    expect(dayBucket(null, NOW)).toBe("older");
    expect(dayBucket("not a date", NOW)).toBe("older");
  });
});

describe("formatListTime", () => {
  it("shows the time of day for today", () => {
    expect(formatListTime(new Date(2026, 8, 24, 8, 7), "de-DE", "gestern", NOW)).toBe("08:07");
  });

  it("uses the given label for yesterday", () => {
    expect(formatListTime(new Date(2026, 8, 23, 16, 20), "de-DE", "gestern", NOW)).toBe("gestern");
  });

  it("shows weekday and time within the last week", () => {
    // ICU versions differ on the abbreviation dot ("Mo" vs "Mo.").
    expect(formatListTime(new Date(2026, 8, 21, 16, 20), "de-DE", "gestern", NOW)).toMatch(
      /^Mo\.? 16:20$/,
    );
  });

  it("shows a short date for older values, with the year only when it differs", () => {
    expect(formatListTime(new Date(2026, 5, 3, 9, 0), "de-DE", "gestern", NOW)).toBe("03.06.");
    expect(formatListTime(new Date(2025, 11, 8, 9, 0), "de-DE", "gestern", NOW)).toBe("08.12.25");
  });

  it("renders a dash for missing values", () => {
    expect(formatListTime(null, "de-DE", "gestern", NOW)).toBe("—");
  });
});
