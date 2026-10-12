import { describe, expect, it } from "vitest";
import type { EditWidget } from "../model";
import { WALLBOARD_GAP, WALLBOARD_MAX_ROWS, wallboardHeaderPx, wallboardPadding, wallboardRowHeight, wallboardRows } from "./fit";

const widget = (y: number, h: number): EditWidget => ({
  id: `w${y}-${h}`,
  type_key: "core.notes",
  type_version: 1,
  title_override: null,
  settings: {},
  layout: { lg: { x: 0, y, w: 4, h } },
  pinned: false,
});

describe("wallboardRowHeight", () => {
  it("fits the rows into the viewport: padding, header and the 24 px gaps are subtracted", () => {
    const viewport = { width: 1920, height: 1080 };
    const header = wallboardHeaderPx(viewport);
    const rows = 6;
    const h = wallboardRowHeight(viewport, rows, header);
    const used = h * rows + (rows - 1) * WALLBOARD_GAP + header + wallboardPadding(viewport).y * 2;
    expect(used).toBeLessThanOrEqual(1080);
    expect(used).toBeGreaterThan(1080 - rows); // floor loses less than a pixel per row
    expect(h).toBe(Math.floor((1080 - 80 - header - 5 * WALLBOARD_GAP) / 6));
  });

  it("clamps to 48..160", () => {
    expect(wallboardRowHeight({ width: 1920, height: 1080 }, 1, 96)).toBe(160);
    expect(wallboardRowHeight({ width: 1920, height: 1080 }, 40, 96)).toBe(48);
    expect(wallboardRowHeight({ width: 320, height: 200 }, 6, 96)).toBe(48);
  });

  it("treats fewer than one row as one", () => {
    expect(wallboardRowHeight({ width: 1920, height: 1080 }, 0, 96)).toBe(wallboardRowHeight({ width: 1920, height: 1080 }, 1, 96));
  });
});

describe("wallboardPadding", () => {
  it("is 40/48 px at 1920 wide and scales with the width", () => {
    expect(wallboardPadding({ width: 1920 })).toEqual({ x: 48, y: 40 });
    expect(wallboardPadding({ width: 960 })).toEqual({ x: 24, y: 20 });
  });
});

describe("wallboardRows", () => {
  it("is the lowest bottom edge of the lg rects, at least 1", () => {
    expect(wallboardRows([])).toBe(1);
    expect(wallboardRows([widget(0, 3), widget(3, 4), widget(1, 2)])).toBe(7);
  });
  it("exports the warning threshold", () => {
    expect(WALLBOARD_MAX_ROWS).toBe(14);
  });
});
