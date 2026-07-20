import { describe, it, expect } from "vitest";
import { queueHasWork, selectQueueShortcuts } from "./DashboardPage";

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
