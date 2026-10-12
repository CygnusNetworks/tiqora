import { describe, expect, it } from "vitest";
import { canDrill, drillHref, validateDrillSearch } from "./drillLinks";

describe("drillHref", () => {
  it("carries ids only, and the chart point when given", () => {
    expect(drillHref(7, "w-abc")).toEqual({ to: "/agent/tickets/drill", search: { d: 7, w: "w-abc" } });
    expect(drillHref(7, "w-abc", { queue: 3 }).search).toEqual({ d: 7, w: "w-abc", point: { queue: 3 } });
  });
});

describe("canDrill", () => {
  it("is live in view and TV mode only", () => {
    expect(canDrill("view")).toBe(true);
    expect(canDrill("wallboard")).toBe(true);
    expect(canDrill("edit")).toBe(false);
    expect(canDrill("preview")).toBe(false);
  });
});

describe("validateDrillSearch", () => {
  it("accepts the router's JSON-parsed values (numbers for numeric strings)", () => {
    expect(validateDrillSearch({ d: 7, w: 123456789, point: { queue: 2, created: "2026-10-05" }, offset: 50, sort: "age", order: "asc" })).toEqual({
      d: 7,
      w: "123456789",
      point: { queue: 2, created: "2026-10-05" },
      sort: "age",
      order: "asc",
      offset: 50,
    });
  });

  it("reads a point that arrives as a JSON string", () => {
    expect(validateDrillSearch({ d: "7", w: "x", point: '{"queue":2}' }).point).toEqual({ queue: 2 });
  });

  it("drops what is not a flat object of scalars, and bad paging", () => {
    const out = validateDrillSearch({ d: -1, w: 5.5, point: { a: { b: 1 } }, offset: -3, sort: "nope", order: "up" });
    expect(out).toEqual({ d: 0, w: "5.5", point: undefined, sort: undefined, order: undefined, offset: undefined });
    expect(validateDrillSearch({ d: 1, w: "x", point: [1] }).point).toBeUndefined();
    expect(validateDrillSearch({ d: 1, w: "x", point: "not json" }).point).toBeUndefined();
    expect(validateDrillSearch({ d: 1, w: "x", point: {} }).point).toBeUndefined();
  });
});
