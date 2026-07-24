import { describe, it, expect } from "vitest";
import type { AppointmentOut } from "@/lib/api";
import { defaultFormValue, formValueFromAppointment } from "./appointmentForm";

describe("defaultFormValue", () => {
  it("uses the given start/end and formats them as datetime-local strings", () => {
    const start = new Date(2026, 5, 1, 9, 0);
    const end = new Date(2026, 5, 1, 10, 30);
    const value = defaultFormValue(3, start, end);
    expect(value).toEqual({
      calendar_id: 3,
      title: "",
      description: "",
      location: "",
      start_time: "2026-06-01T09:00",
      end_time: "2026-06-01T10:30",
      all_day: false,
      recurrence: { type: "", interval: 1, count: "", until: "" },
    });
  });

  it("defaults end to one hour after start when only start is given", () => {
    const start = new Date(2026, 5, 1, 9, 0);
    const value = defaultFormValue(1, start);
    expect(value.start_time).toBe("2026-06-01T09:00");
    expect(value.end_time).toBe("2026-06-01T10:00");
  });

  it("defaults start and end to now / now+1h when neither is given", () => {
    const before = Date.now();
    const value = defaultFormValue(1);
    const after = Date.now();
    const parsedStart = new Date(value.start_time.replace("T", " ")).getTime();
    expect(parsedStart).toBeGreaterThanOrEqual(before - 60000);
    expect(parsedStart).toBeLessThanOrEqual(after + 60000);
    expect(value.end_time > value.start_time).toBe(true);
  });
});

describe("formValueFromAppointment", () => {
  function makeAppointment(overrides: Partial<AppointmentOut> = {}): AppointmentOut {
    return {
      id: 1,
      calendar_id: 3,
      title: "Kickoff",
      description: null,
      location: null,
      start_time: "2026-06-01T09:00:00Z",
      end_time: "2026-06-01T10:00:00Z",
      all_day: false,
      recur_type: null,
      recur_interval: null,
      recur_count: null,
      recur_until: null,
      ...overrides,
    } as AppointmentOut;
  }

  it("maps a plain appointment into a form value, substituting empty strings for null fields", () => {
    const appt = makeAppointment();
    const value = formValueFromAppointment(appt);
    expect(value.calendar_id).toBe(3);
    expect(value.title).toBe("Kickoff");
    expect(value.description).toBe("");
    expect(value.location).toBe("");
    expect(value.all_day).toBe(false);
    expect(value.recurrence).toEqual({ type: "", interval: 1, count: "", until: "" });
  });

  it("preserves description/location text and recurrence fields when present", () => {
    const appt = makeAppointment({
      description: "Discuss roadmap",
      location: "Room 4",
      recur_type: "weekly",
      recur_interval: 2,
      recur_count: 5,
      recur_until: "2026-08-01T00:00:00Z",
    });
    const value = formValueFromAppointment(appt);
    expect(value.description).toBe("Discuss roadmap");
    expect(value.location).toBe("Room 4");
    expect(value.recurrence.type).toBe("weekly");
    expect(value.recurrence.interval).toBe(2);
    expect(value.recurrence.count).toBe("5");
    expect(value.recurrence.until).not.toBe("");
  });

  it("returns empty strings for unparsable or missing date fields", () => {
    const appt = makeAppointment({ start_time: "not-a-date", end_time: null as unknown as string });
    const value = formValueFromAppointment(appt);
    expect(value.start_time).toBe("");
    expect(value.end_time).toBe("");
  });
});
