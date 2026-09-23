import { describe, it, expect } from "vitest";
import {
  detectQueryType,
  formatCustomerLabel,
  isFilterComposition,
  matchQueues,
  parseDate,
  parseKeyed,
  uniqueQueueMatch,
} from "./smartSearch";

describe("parseKeyed / isFilterComposition", () => {
  it("parses queue: fragments", () => {
    expect(parseKeyed("queue:cn-nord")).toEqual({ key: "queue", frag: "cn-nord" });
    expect(parseKeyed("kunde:z90001")).toEqual({ key: "customer", frag: "z90001" });
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
    expect(isFilterComposition("cn-nord")).toBe(false);
  });
});

describe("matchQueues / uniqueQueueMatch", () => {
  const queues = [
    { id: 1, name: "Raw" },
    { id: 2, name: "CN::cn-nord" },
    { id: 3, name: "cn-nord" },
    { id: 4, name: "support" },
  ];

  it("ranks leaf/exact matches first", () => {
    const m = matchQueues(queues, "cn-nord");
    expect(m.map((q) => q.id)).toEqual([3, 2]);
  });

  it("auto-commits exact leaf match even when multiple include the frag", () => {
    expect(uniqueQueueMatch(queues, "cn-nord")?.id).toBe(3);
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
    expect(formatCustomerLabel("Marcus", "z90001")).toBe("Marcus · z90001");
  });
});

describe("detectQueryType", () => {
  it("recognises a ticket number, with or without a leading #", () => {
    expect(detectQueryType("2026010110000042")).toEqual({
      kind: "ticket",
      value: "2026010110000042",
      raw: "2026010110000042",
    });
    expect(detectQueryType(" #2026010110000042 ")?.value).toBe("2026010110000042");
  });

  it("keeps reference numbers and short digit runs as free text", () => {
    // A DFN-CERT reference out of a real subject line — inner separators are
    // never stripped, so this never masquerades as a ticket number.
    expect(detectQueryType("2601-000-0001")?.kind).toBe("text");
    expect(detectQueryType("123456")?.kind).toBe("text");
  });

  it("recognises an e-mail address", () => {
    expect(detectQueryType("m.muster@uni.example.org")).toEqual({
      kind: "email",
      value: "m.muster@uni.example.org",
      raw: "m.muster@uni.example.org",
    });
    expect(detectQueryType("m.muster@uni-example")?.kind).toBe("text");
  });

  it("recognises a date and normalises it to ISO", () => {
    expect(detectQueryType("22.09.2026")).toEqual({
      kind: "date",
      value: "2026-09-22",
      raw: "22.09.2026",
    });
    expect(detectQueryType("2026-09-22")?.value).toBe("2026-09-22");
  });

  it("returns null for empty input and while a filter token is composed", () => {
    expect(detectQueryType("")).toBeNull();
    expect(detectQueryType("   ")).toBeNull();
    // "queue:stw" belongs to the chip typeahead, not to type detection.
    expect(detectQueryType("queue:stw")).toBeNull();
  });

  it("falls back to free text", () => {
    expect(detectQueryType("Router gesperrt")).toEqual({
      kind: "text",
      value: "Router gesperrt",
      raw: "Router gesperrt",
    });
  });
});

describe("parseDate", () => {
  it("accepts both ISO and German notation", () => {
    expect(parseDate("2026-09-22")).toBe("2026-09-22");
    expect(parseDate("1.2.2026")).toBe("2026-02-01");
    expect(parseDate("nope")).toBeNull();
  });
});
