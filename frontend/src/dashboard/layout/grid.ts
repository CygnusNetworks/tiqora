import { GAP, MAX_ROWS, ROW_H, type LayoutItem, type Rect } from "../model";

const cmp = (a: LayoutItem, b: LayoutItem): number =>
  a.rect.y - b.rect.y || a.rect.x - b.rect.x || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

export function clampRect(r: Rect, cols: number, min: [number, number], max: [number, number]): Rect {
  const w = Math.max(Math.min(min[0], cols), Math.min(r.w, max[0], cols));
  const h = Math.max(min[1], Math.min(r.h, max[1]));
  return { x: Math.max(0, Math.min(r.x, cols - w)), y: Math.max(0, r.y), w, h };
}

/** Strict overlap; touching edges do not collide. */
export function collides(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/**
 * Place items one by one, the `first` ids before the rest (each group in (y, x, id) order);
 * an item overlapping an already placed one is pushed below it. The very first item never moves.
 */
export function placeWithPriority(items: LayoutItem[], first: ReadonlySet<string>): LayoutItem[] {
  const order = [...items.filter((i) => first.has(i.id)).sort(cmp), ...items.filter((i) => !first.has(i.id)).sort(cmp)];
  const placed: LayoutItem[] = [];
  for (const item of order) {
    const rect = { ...item.rect };
    let moved = true;
    while (moved) {
      moved = false;
      for (const p of placed) {
        if (collides(rect, p.rect)) {
          rect.y = p.rect.y + p.rect.h;
          moved = true;
        }
      }
    }
    placed.push({ ...item, rect });
  }
  return items.map((i) => placed.find((p) => p.id === i.id)!);
}

/**
 * `fixedId` keeps its rect; every item overlapping an already placed one is pushed below it
 * (cascading). Deterministic: the others are placed in (y, x, id) order.
 */
export function resolveCollisions(items: LayoutItem[], fixedId: string, _cols: number): LayoutItem[] {
  return placeWithPriority(items, new Set([fixedId]));
}

/** Vertical gravity in (y, x, id) order; `fixedId` is not moved. */
export function compactUp(items: LayoutItem[], _cols: number, fixedId?: string): LayoutItem[] {
  const fixed = fixedId === undefined ? undefined : items.find((i) => i.id === fixedId);
  const order = [...items].filter((i) => i !== fixed).sort(cmp);
  const placed: LayoutItem[] = fixed ? [fixed] : [];
  const out = new Map<string, LayoutItem>();
  if (fixed) out.set(fixed.id, fixed);
  for (const item of order) {
    const rect = { ...item.rect };
    while (rect.y > 0 && !placed.some((p) => collides({ ...rect, y: rect.y - 1 }, p.rect))) rect.y -= 1;
    const next = { ...item, rect };
    placed.push(next);
    out.set(item.id, next);
  }
  return items.map((i) => out.get(i.id)!);
}

function withRect(items: LayoutItem[], id: string, rect: Rect): LayoutItem[] {
  return items.map((i) => (i.id === id ? { ...i, rect } : i));
}

export function moveItem(items: LayoutItem[], id: string, to: { x: number; y: number }, cols: number): LayoutItem[] {
  const it = items.find((i) => i.id === id);
  if (!it) return items;
  const rect = clampRect({ ...it.rect, x: to.x, y: to.y }, cols, it.min, it.max);
  return compactUp(resolveCollisions(withRect(items, id, rect), id, cols), cols, id);
}

export function resizeItem(items: LayoutItem[], id: string, size: { w: number; h: number }, cols: number): LayoutItem[] {
  const it = items.find((i) => i.id === id);
  if (!it) return items;
  const rect = clampRect({ ...it.rect, w: size.w, h: size.h }, cols, it.min, it.max);
  return compactUp(resolveCollisions(withRect(items, id, rect), id, cols), cols, id);
}

/** Row-major scan; always terminates (appends below the lowest item). */
export function firstFreeSpot(items: LayoutItem[], w: number, h: number, cols: number): { x: number; y: number } {
  const width = Math.min(w, cols);
  const bottom = items.reduce((m, i) => Math.max(m, i.rect.y + i.rect.h), 0);
  for (let y = 0; y <= bottom; y++) {
    for (let x = 0; x <= cols - width; x++) {
      if (!items.some((i) => collides({ x, y, w: width, h }, i.rect))) return { x, y };
    }
  }
  return { x: 0, y: bottom };
}

export function pixelsToCell(p: { x: number; y: number }, grid: { width: number; cols: number }): { x: number; y: number } {
  const cellW = (grid.width - GAP * (grid.cols - 1)) / grid.cols;
  return {
    x: Math.max(0, Math.round(p.x / (cellW + GAP))),
    y: Math.max(0, Math.round(p.y / (ROW_H + GAP))),
  };
}

/**
 * Exact mirror of backend `normalize_rects` (tiqora.dashboard.layout): clamp to `cols`, place in
 * (y, x, id) order, push overlapping rects below the placed one, no min/max, no compaction.
 * `tooTall` = the backend would reject the layout ("Das Layout ist zu hoch.", y + h > 1000).
 * Shared cases: normalizeCases.json (same expectations as backend/tests/test_dashboard_layout.py).
 */
export function normalizeRects(
  items: { id: string; rect: Rect }[],
  cols: number,
): { rects: Map<string, Rect>; tooTall: boolean } {
  const clamped = items.map(({ id, rect }) => {
    const w = Math.max(1, Math.min(rect.w, cols));
    return { id, x: Math.max(0, Math.min(rect.x, cols - w)), y: rect.y, w, h: Math.max(1, rect.h) };
  });
  clamped.sort((a, b) => a.y - b.y || a.x - b.x || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const placed: Rect[] = [];
  const rects = new Map<string, Rect>();
  let tooTall = false;
  for (const c of clamped) {
    const r: Rect = { x: c.x, y: c.y, w: c.w, h: c.h };
    let moved = true;
    while (moved) {
      moved = false;
      for (const p of placed) {
        if (collides(r, p)) {
          r.y = p.y + p.h;
          moved = true;
        }
      }
    }
    placed.push(r);
    rects.set(c.id, r);
    if (r.y + r.h > MAX_ROWS) tooTall = true;
  }
  return { rects, tooTall };
}
