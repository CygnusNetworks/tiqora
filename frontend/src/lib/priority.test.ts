import { describe, it, expect } from "vitest";
import { priorityColorVar, priorityIdFromName, priorityName } from "./priority";

describe("priorityName", () => {
  it("strips the leading numeric rank", () => {
    expect(priorityName("3 normal")).toBe("normal");
    expect(priorityName("5 very high")).toBe("very high");
    expect(priorityName("1 very low")).toBe("very low");
  });

  it("returns null for empty input", () => {
    expect(priorityName(null)).toBeNull();
    expect(priorityName(undefined)).toBeNull();
    expect(priorityName("")).toBeNull();
  });
});

describe("priorityIdFromName", () => {
  it("parses the leading rank", () => {
    expect(priorityIdFromName("5 very high")).toBe(5);
    expect(priorityIdFromName("3 normal")).toBe(3);
  });

  it("returns null when no rank is present", () => {
    expect(priorityIdFromName("normal")).toBeNull();
    expect(priorityIdFromName(null)).toBeNull();
  });
});

describe("priorityColorVar", () => {
  it("maps ids 1..5 to the matching CSS var", () => {
    expect(priorityColorVar(1)).toBe("var(--color-prio-1)");
    expect(priorityColorVar(2)).toBe("var(--color-prio-2)");
    expect(priorityColorVar(3)).toBe("var(--color-prio-3)");
    expect(priorityColorVar(4)).toBe("var(--color-prio-4)");
    expect(priorityColorVar(5)).toBe("var(--color-prio-5)");
  });

  it("clamps out-of-range ids", () => {
    expect(priorityColorVar(0)).toBe("var(--color-prio-1)");
    expect(priorityColorVar(9)).toBe("var(--color-prio-5)");
    expect(priorityColorVar(-2)).toBe("var(--color-prio-1)");
  });

  it("defaults null/unknown to prio-3", () => {
    expect(priorityColorVar(null)).toBe("var(--color-prio-3)");
    expect(priorityColorVar(undefined)).toBe("var(--color-prio-3)");
    expect(priorityColorVar(Number.NaN)).toBe("var(--color-prio-3)");
  });
});
