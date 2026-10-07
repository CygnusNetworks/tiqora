import { afterEach, describe, expect, it } from "vitest";
import {
  addDaysYmd,
  browserTimeZone,
  displayTimeZone,
  effectiveTimeZone,
  fromZonedInputValue,
  isValidTimeZone,
  listTimeZones,
  setDisplayTimeZone,
  toZonedInputValue,
  wallClockDate,
  ymdInZone,
  zonedDayEndIso,
  zonedDayStartIso,
  zonedWallTimeToUtc,
} from "./timeZone";

afterEach(() => setDisplayTimeZone(null));

describe("display zone", () => {
  it("follows the browser until a valid preference is set", () => {
    expect(displayTimeZone()).toBe(browserTimeZone());
    expect(setDisplayTimeZone("Asia/Tokyo")).toBe("Asia/Tokyo");
    expect(displayTimeZone()).toBe("Asia/Tokyo");
    setDisplayTimeZone(null);
    expect(displayTimeZone()).toBe(browserTimeZone());
  });

  it("ignores zones Intl does not know", () => {
    expect(isValidTimeZone("Mars/Olympus_Mons")).toBe(false);
    expect(setDisplayTimeZone("Mars/Olympus_Mons")).toBe(browserTimeZone());
    expect(effectiveTimeZone("Mars/Olympus_Mons")).toBe(browserTimeZone());
    expect(effectiveTimeZone("Europe/Berlin")).toBe("Europe/Berlin");
  });

  it("lists IANA zones including UTC and Europe/Berlin", () => {
    const zones = listTimeZones();
    expect(zones).toContain("UTC");
    expect(zones).toContain("Europe/Berlin");
  });
});

describe("ymdInZone", () => {
  it("takes the calendar day in the given zone, not the browser's", () => {
    expect(ymdInZone("2026-07-01T22:30:00Z", "Europe/Berlin")).toBe("2026-07-02");
    expect(ymdInZone("2026-07-01T22:30:00Z", "America/New_York")).toBe("2026-07-01");
    expect(ymdInZone(new Date("2026-01-01T10:59:00Z"), "Pacific/Kiritimati")).toBe("2026-01-02");
  });
});

describe("addDaysYmd", () => {
  it("crosses month and year boundaries", () => {
    expect(addDaysYmd("2026-01-31", 1)).toBe("2026-02-01");
    expect(addDaysYmd("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDaysYmd("2026-03-01", -1)).toBe("2026-02-28");
  });
});

describe("zonedWallTimeToUtc", () => {
  it("applies summer and winter offsets", () => {
    expect(zonedWallTimeToUtc("2026-07-01", "08:00", "Europe/Berlin")?.toISOString()).toBe(
      "2026-07-01T06:00:00.000Z",
    );
    expect(zonedWallTimeToUtc("2026-01-15", "08:00", "Europe/Berlin")?.toISOString()).toBe(
      "2026-01-15T07:00:00.000Z",
    );
    expect(zonedWallTimeToUtc("2026-07-01", "08:00", "America/Los_Angeles")?.toISOString()).toBe(
      "2026-07-01T15:00:00.000Z",
    );
  });

  it("moves a time in the spring-forward gap past the gap", () => {
    // 2026-03-29 02:00 → 03:00 in Berlin; 02:30 does not exist.
    expect(zonedWallTimeToUtc("2026-03-29", "02:30", "Europe/Berlin")?.toISOString()).toBe(
      "2026-03-29T01:30:00.000Z",
    );
    expect(zonedWallTimeToUtc("2026-03-29", "03:00", "Europe/Berlin")?.toISOString()).toBe(
      "2026-03-29T01:00:00.000Z",
    );
  });

  it("picks the first occurrence of an ambiguous autumn time", () => {
    // 2026-10-25 03:00 → 02:00 in Berlin; 02:30 happens twice.
    expect(zonedWallTimeToUtc("2026-10-25", "02:30", "Europe/Berlin")?.toISOString()).toBe(
      "2026-10-25T00:30:00.000Z",
    );
    expect(zonedWallTimeToUtc("2026-10-25", "03:00", "Europe/Berlin")?.toISOString()).toBe(
      "2026-10-25T02:00:00.000Z",
    );
  });

  it("rejects malformed input", () => {
    expect(zonedWallTimeToUtc("26-1-1", "08:00", "UTC")).toBeNull();
    expect(zonedWallTimeToUtc("2026-01-01", "8h", "UTC")).toBeNull();
  });
});

describe("zoned day bounds", () => {
  it("covers a full 23-hour DST day", () => {
    expect(zonedDayStartIso("2026-03-29", "Europe/Berlin")).toBe("2026-03-28T23:00:00.000Z");
    expect(zonedDayEndIso("2026-03-29", "Europe/Berlin")).toBe("2026-03-29T21:59:59.999Z");
  });

  it("covers a full 25-hour DST day", () => {
    expect(zonedDayStartIso("2026-10-25", "Europe/Berlin")).toBe("2026-10-24T22:00:00.000Z");
    expect(zonedDayEndIso("2026-10-25", "Europe/Berlin")).toBe("2026-10-25T22:59:59.999Z");
  });
});

describe("datetime-local values", () => {
  it("round-trips through the zone", () => {
    const iso = "2026-07-01T22:30:00.000Z";
    expect(toZonedInputValue(iso, "Europe/Berlin")).toBe("2026-07-02T00:30");
    expect(toZonedInputValue(iso, "America/New_York")).toBe("2026-07-01T18:30");
    expect(fromZonedInputValue("2026-07-02T00:30", "Europe/Berlin")?.toISOString()).toBe(iso);
    expect(fromZonedInputValue("2026-07-01T18:30", "America/New_York")?.toISOString()).toBe(iso);
  });

  it("is blank or null for missing values", () => {
    expect(toZonedInputValue(null, "UTC")).toBe("");
    expect(toZonedInputValue("garbage", "UTC")).toBe("");
    expect(fromZonedInputValue("", "UTC")).toBeNull();
    expect(fromZonedInputValue("2026-07-01", "UTC")).toBeNull();
  });
});

describe("wallClockDate", () => {
  it("exposes the zone's wall clock as local fields", () => {
    const d = wallClockDate(new Date("2026-07-01T22:30:00Z"), "Asia/Tokyo");
    expect([d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), d.getMinutes()]).toEqual([
      2026, 6, 2, 7, 30,
    ]);
  });
});
