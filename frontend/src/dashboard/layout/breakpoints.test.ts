import { expect, test } from "vitest";
import type { EditWidget, Rect } from "../model";
import { breakpointForWidth, layoutTooTall, layoutForBreakpoint, smOrder } from "./breakpoints";
import { collides } from "./grid";

const w = (id: string, lg: Rect): EditWidget => ({
  id,
  type_key: "core.counter",
  type_version: 1,
  title_override: null,
  settings: {},
  layout: { lg },
});

test("breakpointForWidth", () => {
  expect([1200, 900, 899, 520, 519].map(breakpointForWidth)).toEqual(["lg", "lg", "md", "md", "sm"]);
});

test("md layout scales lg and never overlaps", () => {
  const ws = [
    w("a", { x: 0, y: 0, w: 3, h: 2 }),
    w("b", { x: 3, y: 0, w: 3, h: 2 }),
    w("c", { x: 6, y: 0, w: 3, h: 2 }),
    w("d", { x: 9, y: 0, w: 3, h: 2 }),
  ];
  const m = layoutForBreakpoint(ws, "md", () => [1, 1]);
  expect([...m.values()].every((r) => r.x + r.w <= 8)).toBe(true);
  expect([...m.values()].map((r) => r.x)).toEqual([0, 2, 4, 6]);
  for (const [i, a] of [...m.values()].entries()) for (const b of [...m.values()].slice(i + 1)) expect(collides(a, b)).toBe(false);
});

test("derived md rects that would collide are pushed down and compacted", () => {
  const ws = [w("a", { x: 0, y: 0, w: 12, h: 2 }), w("b", { x: 0, y: 0, w: 12, h: 2 }), w("c", { x: 0, y: 9, w: 6, h: 2 })];
  const m = layoutForBreakpoint(ws, "md", () => [1, 1]);
  expect(m.get("a")).toEqual({ x: 0, y: 0, w: 8, h: 2 });
  expect(m.get("b")).toEqual({ x: 0, y: 2, w: 8, h: 2 });
  expect(m.get("c")).toMatchObject({ y: 4 });
});

test("an explicit md rect wins over the derived one", () => {
  const ws = [{ ...w("a", { x: 0, y: 0, w: 6, h: 2 }), layout: { lg: { x: 0, y: 0, w: 6, h: 2 }, md: { x: 0, y: 5, w: 8, h: 2 } } }];
  expect(layoutForBreakpoint(ws, "md", () => [1, 1]).get("a")).toEqual({ x: 0, y: 5, w: 8, h: 2 });
});

test("a derived md rect is pushed below an explicit one, which never moves", () => {
  const ws = [
    { ...w("a", { x: 0, y: 0, w: 12, h: 2 }), layout: { lg: { x: 0, y: 0, w: 12, h: 2 }, md: { x: 0, y: 3, w: 8, h: 2 } } },
    w("b", { x: 0, y: 3, w: 6, h: 2 }),
  ];
  const m = layoutForBreakpoint(ws, "md", () => [1, 1]);
  expect(m.get("a")).toEqual({ x: 0, y: 3, w: 8, h: 2 });
  expect(m.get("b")).toEqual({ x: 0, y: 5, w: 4, h: 2 });
});

test("an lg rect wider than 12 from a bad payload is clamped", () => {
  expect(layoutForBreakpoint([w("a", { x: 10, y: 0, w: 6, h: 2 })], "lg", () => [1, 1]).get("a")).toEqual({ x: 6, y: 0, w: 6, h: 2 });
});

test("lg output is overlap free and agrees with the backend rule", () => {
  const m = layoutForBreakpoint([w("b", { x: 0, y: 0, w: 4, h: 2 }), w("a", { x: 0, y: 0, w: 4, h: 2 })], "lg", () => [1, 1]);
  expect(m.get("a")).toEqual({ x: 0, y: 0, w: 4, h: 2 });
  expect(m.get("b")).toEqual({ x: 0, y: 2, w: 4, h: 2 });
});

test("smOrder is the array order, not the visual order", () => {
  expect(smOrder([w("b", { x: 0, y: 5, w: 3, h: 1 }), w("a", { x: 0, y: 0, w: 3, h: 1 })])).toEqual(["b", "a"]);
});

test("layoutTooTall flags lg and md layouts the backend would reject", () => {
  expect(layoutTooTall([w("a", { x: 0, y: 0, w: 3, h: 2 })])).toBe(false);
  expect(layoutTooTall([w("a", { x: 0, y: 990, w: 3, h: 24 })])).toBe(true);
  expect(layoutTooTall([{ ...w("a", { x: 0, y: 0, w: 3, h: 2 }), layout: { lg: { x: 0, y: 0, w: 3, h: 2 }, md: { x: 0, y: 999, w: 3, h: 24 } } }])).toBe(true);
});
