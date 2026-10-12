import { describe, expect, test } from "vitest";
import type { LayoutItem } from "../model";
import { firstFreeSpot } from "./grid";
import data from "./firstFreeSpotCases.json";

type Case = { name: string; cols: number; rects: number[][]; w: number; h: number; expected: number[] };
const cases = data.cases as Case[];

describe("firstFreeSpot mirrors backend first_free_spot (firstFreeSpotCases.json)", () => {
  test("the shared fixture is not empty", () => {
    expect(cases.length).toBeGreaterThan(3);
  });

  for (const c of cases) {
    test(c.name, () => {
      const items: LayoutItem[] = c.rects.map(([x, y, w, h], i) => ({ id: `r${i}`, rect: { x, y, w, h }, min: [1, 1], max: [12, 20] }));
      expect(firstFreeSpot(items, c.w, c.h, c.cols)).toEqual({ x: c.expected[0], y: c.expected[1] });
    });
  }
});
