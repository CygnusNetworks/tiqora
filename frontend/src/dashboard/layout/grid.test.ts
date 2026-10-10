import { describe, expect, test } from "vitest";
import type { LayoutItem, Rect } from "../model";
import cases from "./normalizeCases.json";
import {
  clampRect,
  collides,
  compactUp,
  firstFreeSpot,
  moveItem,
  normalizeRects,
  pixelsToCell,
  resizeItem,
  resolveCollisions,
} from "./grid";

const it = (id: string, x: number, y: number, w: number, h: number): LayoutItem => ({
  id,
  rect: { x, y, w, h },
  min: [2, 1],
  max: [12, 8],
});
const rects = (items: LayoutItem[]): Record<string, Rect> => Object.fromEntries(items.map((i) => [i.id, i.rect]));

test("clampRect keeps the rect inside the grid and inside min/max", () => {
  expect(clampRect({ x: 10, y: -3, w: 6, h: 20 }, 12, [2, 1], [12, 8])).toEqual({ x: 6, y: 0, w: 6, h: 8 });
  expect(clampRect({ x: 0, y: 0, w: 1, h: 1 }, 12, [3, 2], [12, 8])).toEqual({ x: 0, y: 0, w: 3, h: 2 });
});

test("touching rects do not collide, overlapping ones do", () => {
  expect(collides({ x: 0, y: 0, w: 6, h: 2 }, { x: 6, y: 0, w: 6, h: 2 })).toBe(false);
  expect(collides({ x: 0, y: 0, w: 6, h: 2 }, { x: 5, y: 1, w: 6, h: 2 })).toBe(true);
});

test("moveItem pushes the item below down and cascades", () => {
  const out = moveItem([it("a", 0, 0, 6, 2), it("b", 0, 2, 6, 2), it("c", 0, 4, 6, 2)], "c", { x: 0, y: 0 }, 12);
  expect(rects(out)).toEqual({ c: { x: 0, y: 0, w: 6, h: 2 }, a: { x: 0, y: 2, w: 6, h: 2 }, b: { x: 0, y: 4, w: 6, h: 2 } });
});

test("compactUp closes holes left by a move, never past the fixed item", () => {
  const items = [it("a", 0, 0, 6, 2), it("b", 0, 6, 6, 2)];
  expect(rects(compactUp(items, 12)).b.y).toBe(2);
  const fixed = rects(compactUp(items, 12, "b"));
  expect(fixed.b.y).toBe(6);
  expect(fixed.a.y).toBe(0);
  // an item below the fixed one stops at it instead of tunnelling through
  const below = rects(compactUp([it("f", 0, 4, 6, 2), it("g", 0, 9, 6, 2)], 12, "f"));
  expect(below).toEqual({ f: { x: 0, y: 4, w: 6, h: 2 }, g: { x: 0, y: 6, w: 6, h: 2 } });
});

test("resizeItem respects min and max and pushes neighbours", () => {
  const out = resizeItem([it("a", 0, 0, 3, 2), it("b", 0, 2, 3, 2)], "a", { w: 20, h: 5 }, 12);
  expect(rects(out).a).toEqual({ x: 0, y: 0, w: 12, h: 5 });
  expect(rects(out).b.y).toBe(5);
});

test("firstFreeSpot finds the first gap row-major and appends below when full", () => {
  expect(firstFreeSpot([it("a", 0, 0, 6, 2)], 6, 2, 12)).toEqual({ x: 6, y: 0 });
  expect(firstFreeSpot([it("a", 0, 0, 12, 2)], 6, 2, 12)).toEqual({ x: 0, y: 2 });
  expect(firstFreeSpot([], 3, 2, 12)).toEqual({ x: 0, y: 0 });
});

test("pixelsToCell snaps to the nearest cell and clamps at zero", () => {
  expect(pixelsToCell({ x: 205, y: 165 }, { width: 1200, cols: 12 })).toEqual({ x: 2, y: 2 });
  expect(pixelsToCell({ x: -40, y: -9 }, { width: 1200, cols: 12 })).toEqual({ x: 0, y: 0 });
});

test("layout results are deterministic whatever the input order", () => {
  const items = [it("a", 0, 0, 6, 2), it("b", 0, 0, 6, 2), it("c", 3, 1, 6, 2)];
  expect(rects(resolveCollisions(items, "a", 12))).toEqual(rects(resolveCollisions([...items].reverse(), "a", 12)));
});

test("resolveCollisions leaves a clean layout unchanged and terminates on 40 stacked items", () => {
  const clean = [it("a", 0, 0, 6, 2), it("b", 6, 0, 6, 2)];
  expect(resolveCollisions(clean, "a", 12)).toEqual(clean);
  const out = resolveCollisions(Array.from({ length: 40 }, (_, i) => it(`i${i}`, 0, 0, 4, 2)), "i0", 12);
  for (const [i, a] of out.entries()) for (const b of out.slice(i + 1)) expect(collides(a.rect, b.rect)).toBe(false);
});

test("the engine functions do not mutate their input", () => {
  const items = [it("a", 0, 0, 6, 2), it("b", 0, 0, 6, 2), it("c", 3, 7, 6, 2)];
  const before = structuredClone(items);
  resolveCollisions(items, "a", 12);
  compactUp(items, 12, "a");
  moveItem(items, "c", { x: 0, y: 0 }, 12);
  resizeItem(items, "a", { w: 12, h: 4 }, 12);
  firstFreeSpot(items, 3, 3, 12);
  expect(items).toEqual(before);
});

describe("normalizeRects mirrors backend normalize_rects (normalizeCases.json)", () => {
  for (const c of cases.cases) {
    test(c.name, () => {
      const input =
        typeof c.input === "string"
          ? Array.from({ length: 40 }, (_, i): [string, number[]] => [`w${String(i).padStart(2, "0")}`, [0, 999, 12, 24]])
          : (c.input as [string, number[]][]);
      const { rects: out, tooTall } = normalizeRects(
        input.map(([id, [x, y, w, h]]) => ({ id, rect: { x, y, w, h } })),
        c.cols,
      );
      if ("error" in c) {
        expect(tooTall).toBe(true);
        return;
      }
      expect(tooTall).toBe(false);
      const exp = c.expected as Record<string, number[]>;
      expect(Object.fromEntries([...out].map(([id, r]) => [id, [r.x, r.y, r.w, r.h]]))).toEqual(exp);
    });
  }

  test("input order does not matter and the result is idempotent", () => {
    const items = [
      { id: "b", rect: { x: 0, y: 0, w: 4, h: 2 } },
      { id: "a", rect: { x: 0, y: 0, w: 4, h: 2 } },
    ];
    const first = normalizeRects(items, 12).rects;
    expect(normalizeRects([...items].reverse(), 12).rects).toEqual(first);
    expect(normalizeRects([...first].map(([id, rect]) => ({ id, rect })), 12).rects).toEqual(first);
  });

  test("a moved-then-resolved layout is a fixed point of the backend normalisation", () => {
    const out = moveItem([it("a", 0, 0, 6, 2), it("b", 6, 0, 6, 2), it("c", 0, 2, 12, 2)], "c", { x: 0, y: 0 }, 12);
    const again = normalizeRects(out.map((i) => ({ id: i.id, rect: i.rect })), 12).rects;
    expect(Object.fromEntries(again)).toEqual(rects(out));
  });
});
