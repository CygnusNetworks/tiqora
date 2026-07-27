import { describe, it, expect } from "vitest";
import {
  formatCustomerLabel,
  isFilterComposition,
  matchQueues,
  parseKeyed,
  uniqueQueueMatch,
} from "./smartSearch";

describe("parseKeyed / isFilterComposition", () => {
  it("parses queue: fragments", () => {
    expect(parseKeyed("queue:stw-bn")).toEqual({ key: "queue", frag: "stw-bn" });
    expect(parseKeyed("kunde:z26111")).toEqual({ key: "customer", frag: "z26111" });
  });

  it("treats partial keys as filter composition (not free text)", () => {
    expect(isFilterComposition("q")).toBe(true);
    expect(isFilterComposition("que")).toBe(true);
    expect(isFilterComposition("queue")).toBe(true);
    expect(isFilterComposition("queue:stw")).toBe(true);
    expect(isFilterComposition("kunde")).toBe(true);
  });

  it("does not flag normal free text as composition", () => {
    expect(isFilterComposition("printer offline")).toBe(false);
    expect(isFilterComposition("stw-bn")).toBe(false);
  });
});

describe("matchQueues / uniqueQueueMatch", () => {
  const queues = [
    { id: 1, name: "Raw" },
    { id: 2, name: "STW::stw-bn" },
    { id: 3, name: "stw-bn" },
    { id: 4, name: "support" },
  ];

  it("ranks leaf/exact matches first", () => {
    const m = matchQueues(queues, "stw-bn");
    expect(m.map((q) => q.id)).toEqual([3, 2]);
  });

  it("auto-commits exact leaf match even when multiple include the frag", () => {
    expect(uniqueQueueMatch(queues, "stw-bn")?.id).toBe(3);
  });

  it("auto-commits when only one match remains", () => {
    expect(uniqueQueueMatch(queues, "supp")?.id).toBe(4);
  });

  it("returns null when ambiguous", () => {
    expect(uniqueQueueMatch(queues, "stw")).toBeNull();
  });
});

describe("formatCustomerLabel", () => {
  it("appends customer id when missing from name", () => {
    expect(formatCustomerLabel("Marcus", "z26111")).toBe("Marcus · z26111");
  });
});
