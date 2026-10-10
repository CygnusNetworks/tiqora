import { COLS, MAX_H, type Breakpoint, type EditWidget, type LayoutItem, type Rect } from "../model";
import { clampRect, compactUp, normalizeRects, placeWithPriority } from "./grid";

export function breakpointForWidth(containerWidth: number): Breakpoint {
  return containerWidth >= 900 ? "lg" : containerWidth >= 520 ? "md" : "sm";
}

/**
 * Rects per widget at `lg` or `md`.
 * lg: stored lg, clamped to 12 columns and made overlap-free (same rule as the backend).
 * md: stored md where present, else lg scaled by 8/12. If no widget has a stored md the scaled
 * layout is collision-resolved and compacted; with a mix, stored rects are placed first (they
 * are never moved by the derived ones) and the derived ones are pushed below, without compaction.
 */
export function layoutForBreakpoint(
  widgets: EditWidget[],
  bp: "lg" | "md",
  mins: (key: string) => [number, number],
): Map<string, Rect> {
  const cols = COLS[bp];
  const explicit = new Set<string>();
  const items: LayoutItem[] = widgets.map((w) => {
    const min = mins(w.type_key);
    const max: [number, number] = [cols, MAX_H];
    let rect: Rect;
    if (bp === "md" && w.layout.md) {
      explicit.add(w.id);
      rect = w.layout.md;
    } else if (bp === "md") {
      const l = w.layout.lg;
      rect = { x: Math.round((l.x * 8) / 12), y: l.y, w: Math.max(1, Math.round((l.w * 8) / 12)), h: l.h };
    } else {
      rect = w.layout.lg;
    }
    return { id: w.id, rect: clampRect(rect, cols, min, max), min, max };
  });
  if (bp === "md" && explicit.size === 0 && items.length > 0) {
    const resolved = compactUp(placeWithPriority(items, new Set()), cols);
    return new Map(resolved.map((i) => [i.id, i.rect]));
  }
  // lg, or md with stored rects: stored md rects are placed first, derived ones are pushed below
  if (bp === "lg") return normalizeRects(items, cols).rects;
  return new Map(placeWithPriority(items, explicit).map((i) => [i.id, i.rect]));
}

/** Phone order is the array order (not the visual order). */
export function smOrder(widgets: EditWidget[]): string[] {
  return widgets.map((w) => w.id);
}
