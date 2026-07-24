import { describe, expect, it } from "vitest";
import {
  actionCount,
  criteriaCount,
  decodeJob,
  scheduleSummary,
} from "./genericAgentJob";

const WEEKDAYS = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
const LABELS = { daily: "Täglich", hourly: "stündlich", every: "alle" };

describe("decodeJob", () => {
  it("groups keys into schedule / criteria / actions and marks executed vs ignored", () => {
    const job = decodeJob({
      Valid: ["1"],
      ScheduleDays: ["0", "1", "2", "3", "4", "5", "6"],
      ScheduleHours: ["2"],
      ScheduleMinutes: ["0"],
      StateIDs: ["2", "6"],
      TicketCreateTimeOlderMinutes: ["43200"],
      TicketFreeText1: ["VIP"], // not ported → ignored
      NewStateID: ["3"],
      "NewDynamicField_Foo": ["bar"], // starts with New → treated as action prefix
      DynamicField_Bar: ["baz"],
    });

    expect(job.valid).toBe(true);
    expect(job.hasSchedule).toBe(true);
    expect(job.scheduleDays).toEqual([0, 1, 2, 3, 4, 5, 6]);

    const state = job.criteria.find((c) => c.key === "StateIDs");
    expect(state?.values).toEqual(["2", "6"]);
    expect(state?.executed).toBe(true);

    const freetext = job.criteria.find((c) => c.key === "TicketFreeText1");
    expect(freetext?.executed).toBe(false);

    const setState = job.actions.find((a) => a.key === "StateID");
    expect(setState?.executed).toBe(true);

    // DynamicField_* is its own group and always executed.
    expect(job.dynamicFields.map((d) => d.key)).toContain("Bar");
    expect(job.dynamicFields.every((d) => d.executed)).toBe(true);
  });

  it("treats Valid=0 as inactive and an incomplete schedule as manual-only", () => {
    const job = decodeJob({ Valid: ["0"], ScheduleDays: ["1"], StateIDs: ["1"] });
    expect(job.valid).toBe(false);
    expect(job.hasSchedule).toBe(false); // hours/minutes missing
  });

  it("counts criteria and actions for the list badge", () => {
    const job = decodeJob({
      StateIDs: ["1"],
      QueueIDs: ["4"],
      NewStateID: ["3"],
      DynamicField_X: ["y"],
    });
    expect(criteriaCount(job)).toBe(2);
    expect(actionCount(job)).toBe(2); // 1 action + 1 dynamic field
  });
});

describe("scheduleSummary", () => {
  it("collapses a full week to the daily label with a single time", () => {
    const job = decodeJob({
      ScheduleDays: ["0", "1", "2", "3", "4", "5", "6"],
      ScheduleHours: ["2"],
      ScheduleMinutes: ["0"],
    });
    expect(scheduleSummary(job, WEEKDAYS, LABELS)).toBe("Täglich 02:00");
  });

  it("shows Mo–Fr for the work week", () => {
    const job = decodeJob({
      ScheduleDays: ["1", "2", "3", "4", "5"],
      ScheduleHours: ["9"],
      ScheduleMinutes: ["30"],
    });
    expect(scheduleSummary(job, WEEKDAYS, LABELS)).toBe("Mo–Fr, 09:30");
  });

  it("summarises a 24-hour schedule as hourly", () => {
    const hours = Array.from({ length: 24 }, (_, i) => String(i));
    const job = decodeJob({
      ScheduleDays: ["0", "1", "2", "3", "4", "5", "6"],
      ScheduleHours: hours,
      ScheduleMinutes: ["5"],
    });
    expect(scheduleSummary(job, WEEKDAYS, LABELS)).toBe("Täglich stündlich :05");
  });

  it("returns null for a manual-only job", () => {
    const job = decodeJob({ StateIDs: ["1"] });
    expect(scheduleSummary(job, WEEKDAYS, LABELS)).toBeNull();
  });
});
