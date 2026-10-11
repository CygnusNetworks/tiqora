import { describe, it, expect } from "vitest";
import {
  queueHasWork,
  selectQueueShortcuts,
  greetingKey,
  formatDurationSince,
  nearestEscalation,
} from "./legacy";

type Q = { id: number; counts?: { open?: number; new?: number } | null };

describe("queueHasWork", () => {
  it("is false when open and new are both zero or missing", () => {
    expect(queueHasWork({ counts: { open: 0, new: 0 } })).toBe(false);
    expect(queueHasWork({ counts: { open: 0 } })).toBe(false);
    expect(queueHasWork({ counts: {} })).toBe(false);
    expect(queueHasWork({})).toBe(false);
    expect(queueHasWork({ counts: null })).toBe(false);
  });

  it("is true when open or new is positive", () => {
    expect(queueHasWork({ counts: { open: 3, new: 0 } })).toBe(true);
    expect(queueHasWork({ counts: { open: 0, new: 2 } })).toBe(true);
    expect(queueHasWork({ counts: { open: 1, new: 1 } })).toBe(true);
  });
});

describe("selectQueueShortcuts", () => {
  const queues: Q[] = [
    { id: 1, counts: { open: 0, new: 0 } },
    { id: 2, counts: { open: 5, new: 0 } },
    { id: 3, counts: { open: 0, new: 2 } },
    { id: 4, counts: { open: 12, new: 1 } },
    { id: 5, counts: { open: 1, new: 0 } },
  ];

  it("filters out empty queues and ranks by open descending", () => {
    const selected = selectQueueShortcuts(queues);
    expect(selected.map((q) => q.id)).toEqual([4, 2, 5, 3]);
  });

  it("respects the limit", () => {
    const selected = selectQueueShortcuts(queues, 2);
    expect(selected.map((q) => q.id)).toEqual([4, 2]);
  });

  it("returns empty when every queue is empty", () => {
    expect(selectQueueShortcuts([{ id: 9, counts: { open: 0, new: 0 } }])).toEqual([]);
  });
});

describe("greetingKey", () => {
  it("splits morning/day/evening at 11:00 and 18:00", () => {
    expect(greetingKey(new Date(2024, 0, 1, 6, 0))).toBe("morning");
    expect(greetingKey(new Date(2024, 0, 1, 10, 59))).toBe("morning");
    expect(greetingKey(new Date(2024, 0, 1, 11, 0))).toBe("day");
    expect(greetingKey(new Date(2024, 0, 1, 17, 59))).toBe("day");
    expect(greetingKey(new Date(2024, 0, 1, 18, 0))).toBe("evening");
    expect(greetingKey(new Date(2024, 0, 1, 23, 0))).toBe("evening");
  });
});

describe("formatDurationSince", () => {
  it("formats an elapsed duration without a sign", () => {
    const epoch = Math.floor(Date.now() / 1000) - 2 * 3600 - 5 * 60;
    expect(formatDurationSince(epoch)).toBe("2h05m");
  });

  it("formats sub-hour durations as minutes only", () => {
    const epoch = Math.floor(Date.now() / 1000) - 90;
    expect(formatDurationSince(epoch)).toBe("2m");
  });
});

describe("nearestEscalation", () => {
  const none = { escalation_time: 0, escalation_response_time: 0, escalation_update_time: 0, escalation_solution_time: 0 };

  it("is the smallest escalation epoch that is set, past or future", () => {
    expect(nearestEscalation({ ...none, escalation_update_time: 900, escalation_solution_time: 500 })).toBe(500);
    expect(nearestEscalation({ ...none, escalation_time: 2_000_000_000 })).toBe(2_000_000_000);
  });

  it("is null when no escalation is set", () => {
    expect(nearestEscalation(none)).toBeNull();
  });
});
