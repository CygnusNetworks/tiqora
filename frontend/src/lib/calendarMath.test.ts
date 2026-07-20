import { describe, it, expect } from "vitest";
import {
  monthGridDays,
  weekDays,
  startOfWeek,
  isSameDay,
  isCurrentMonth,
  groupByDay,
  dayKey,
} from "./calendarMath";

describe("calendarMath", () => {
  it("monthGridDays returns 42 days starting on Monday", () => {
    const days = monthGridDays(new Date(2026, 1, 15)); // Feb 2026 (starts on a Sunday)
    expect(days).toHaveLength(42);
    expect(days[0].getDay()).toBe(1); // Monday
    expect(isCurrentMonth(days[0], new Date(2026, 1, 15))).toBe(false);
    // Feb 1 2026 is present somewhere in the grid
    expect(days.some((d) => d.getDate() === 1 && d.getMonth() === 1)).toBe(true);
  });

  it("weekDays returns 7 consecutive days starting Monday", () => {
    const days = weekDays(new Date(2026, 2, 4)); // a Wednesday
    expect(days).toHaveLength(7);
    expect(days[0].getDay()).toBe(1);
    expect(days[6].getDay()).toBe(0);
  });

  it("startOfWeek is idempotent for a Monday", () => {
    const monday = new Date(2026, 2, 2);
    expect(monday.getDay()).toBe(1);
    expect(isSameDay(startOfWeek(monday), monday)).toBe(true);
  });

  it("isSameDay ignores time-of-day", () => {
    const a = new Date(2026, 0, 1, 9, 0);
    const b = new Date(2026, 0, 1, 23, 59);
    expect(isSameDay(a, b)).toBe(true);
  });

  it("groupByDay buckets occurrences by local day", () => {
    const items = [
      { start_time: "2026-01-01T09:00:00" },
      { start_time: "2026-01-01T14:00:00" },
      { start_time: "2026-01-02T09:00:00" },
    ];
    const grouped = groupByDay(items);
    expect(grouped.get(dayKey(new Date(2026, 0, 1)))).toHaveLength(2);
    expect(grouped.get(dayKey(new Date(2026, 0, 2)))).toHaveLength(1);
  });
});
