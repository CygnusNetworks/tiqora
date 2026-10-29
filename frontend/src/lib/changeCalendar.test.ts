import { describe, expect, it } from "vitest";
import {
  MAX_RANGE_DAYS,
  dayFreezeLevel,
  freezesForService,
  overlapsDay,
  spanDayKeys,
  stepAnchor,
  viewRange,
  type CalendarView,
} from "./changeCalendar";
import type { FreezeOccurrence } from "./changeApi";

const freeze = (over: Partial<FreezeOccurrence> = {}): FreezeOccurrence => ({
  freeze_id: 1,
  name: "Jahresabschluss",
  enforcement: "block",
  allow_emergency: false,
  scope: "all",
  service_ids: [],
  start: "2026-10-27T00:00:00Z",
  end: "2026-10-29T00:00:00Z",
  ...over,
});

describe("viewRange", () => {
  it.each<[CalendarView, number]>([
    ["month", 42],
    ["week", 7],
    ["service", 14],
  ])("asks for a whole number of days in %s view", (view, count) => {
    const { start, end, days } = viewRange(view, new Date(2026, 9, 29));
    expect(days).toHaveLength(count);
    expect((Date.parse(end) - Date.parse(start)) / 86_400_000).toBe(count);
  });

  it("starts the month grid on the Monday before the 1st, in UTC", () => {
    const { start, end } = viewRange("month", new Date(2026, 9, 29));
    expect(start).toBe("2026-09-28T00:00:00Z");
    expect(end).toBe("2026-11-09T00:00:00Z");
  });

  it("never exceeds 92 days however far the agent navigates", () => {
    for (const view of ["month", "week", "service"] as CalendarView[]) {
      let anchor = new Date(2026, 9, 29);
      for (let i = 0; i < 60; i += 1) {
        anchor = stepAnchor(view, anchor, i % 2 === 0 ? 1 : 3);
        const { start, end } = viewRange(view, anchor);
        expect((Date.parse(end) - Date.parse(start)) / 86_400_000).toBeLessThanOrEqual(MAX_RANGE_DAYS);
      }
    }
  });
});

describe("stepAnchor", () => {
  it("steps a month, a week and two weeks", () => {
    expect(stepAnchor("month", new Date(2026, 9, 29), 1)).toEqual(new Date(2026, 10, 1));
    expect(stepAnchor("month", new Date(2026, 0, 15), -1)).toEqual(new Date(2025, 11, 1));
    expect(stepAnchor("week", new Date(2026, 9, 29), 1)).toEqual(new Date(2026, 10, 5));
    expect(stepAnchor("service", new Date(2026, 9, 29), -1)).toEqual(new Date(2026, 9, 15));
  });
});

describe("spanDayKeys", () => {
  it("lists every UTC day of a window", () => {
    expect(spanDayKeys("2026-10-24T22:00:00Z", "2026-10-26T01:00:00Z")).toEqual(["2026-10-24", "2026-10-25", "2026-10-26"]);
  });

  it("does not count an end at midnight as another day", () => {
    expect(spanDayKeys("2026-10-24T20:00:00Z", "2026-10-25T00:00:00Z")).toEqual(["2026-10-24"]);
  });

  it("keeps an empty window on its day", () => {
    expect(spanDayKeys("2026-10-24T20:00:00Z", "2026-10-24T20:00:00Z")).toEqual(["2026-10-24"]);
  });
});

describe("freeze days", () => {
  it("overlaps by half-open day", () => {
    expect(overlapsDay("2026-10-27T00:00:00Z", "2026-10-29T00:00:00Z", "2026-10-28")).toBe(true);
    expect(overlapsDay("2026-10-27T00:00:00Z", "2026-10-29T00:00:00Z", "2026-10-29")).toBe(false);
  });

  it("lets a blocking freeze win over a warning on the same day", () => {
    const list = [freeze({ enforcement: "warn" }), freeze({ freeze_id: 2, enforcement: "block" })];
    expect(dayFreezeLevel(list, "2026-10-28")).toBe("block");
    expect(dayFreezeLevel([list[0]], "2026-10-28")).toBe("warn");
    expect(dayFreezeLevel(list, "2026-10-30")).toBeNull();
  });

  it("filters by service: global freezes and those naming it", () => {
    const list = [freeze(), freeze({ freeze_id: 2, scope: "services", service_ids: [2] }), freeze({ freeze_id: 3, scope: "services", service_ids: [3] })];
    expect(freezesForService(list, null)).toHaveLength(3);
    expect(freezesForService(list, 2).map((f) => f.freeze_id)).toEqual([1, 2]);
  });
});
