import { describe, it, expect } from "vitest";
import {
  dayBucket,
  formatCalendarDay,
  formatDateOnly,
  formatDateTime,
  formatListTime,
  formatTimeOfDay,
} from "./format";

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

describe("explicit time zones", () => {
  // 22:30 UTC on 1 July: already 2 July in Berlin, still 1 July in New York.
  const LATE_UTC = "2026-07-01T22:30:00Z";

  it("formats date and time in the given zone", () => {
    expect(formatDateTime(LATE_UTC, "de-DE", "Europe/Berlin")).toMatch(/02\.07\.2026.*00:30/);
    expect(formatDateTime(LATE_UTC, "de-DE", "America/New_York")).toMatch(/01\.07\.2026.*18:30/);
    expect(formatDateOnly(LATE_UTC, "de-DE", "Europe/Berlin")).toBe("02.07.2026");
    expect(formatDateOnly(LATE_UTC, "de-DE", "America/New_York")).toBe("01.07.2026");
    expect(formatTimeOfDay(LATE_UTC, "de-DE", "Asia/Tokyo")).toBe("07:30");
  });

  it("buckets by the calendar day of the zone", () => {
    const now = new Date("2026-07-02T08:00:00Z");
    expect(dayBucket(LATE_UTC, now, "Europe/Berlin")).toBe("today");
    expect(dayBucket(LATE_UTC, now, "America/New_York")).toBe("yesterday");
    expect(formatListTime(LATE_UTC, "de-DE", "gestern", now, "Europe/Berlin")).toBe("00:30");
  });

  it("formats YYYY-MM-DD calendar days without shifting them", () => {
    expect(formatCalendarDay("2026-07-01", "de-DE")).toBe("01.07.2026");
    expect(formatCalendarDay("2026-12-31T00:00:00", "en-US")).toBe("12/31/2026");
    expect(formatCalendarDay("", "de-DE")).toBe("—");
  });
});
