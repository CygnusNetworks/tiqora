/**
 * Pointer drag and resize of grid widgets (no library). Pointer capture on the handle; while the
 * gesture runs the hook reports the screen offset of the dragged widget (`ghost`), the snapped
 * target cell and the live layout of all others (`preview`, the same `moveItem`/`resizeItem` the
 * reducer runs). `pointerup` commits exactly one action; `pointercancel` and Escape abort.
 * Pointer maths uses the grid's `getBoundingClientRect` and flips x in a right-to-left grid.
 */
import { useCallback, useEffect, useRef, useState, type PointerEventHandler, type RefObject } from "react";
import { moveItem, pixelsToCell, resizeItem } from "../layout/grid";
import { GAP, ROW_H, type LayoutItem, type Rect } from "../model";

export type DragCommit =
  | { kind: "move"; id: string; to: { x: number; y: number } }
  | { kind: "resize"; id: string; size: { w: number; h: number } };

export interface DragState {
  id: string;
  kind: "move" | "resize";
  /** Screen offset of the pointer since the gesture started (px). */
  ghost: { dx: number; dy: number };
  /** Snapped rect of the dragged widget where it would land. */
  target: Rect;
  /** Rects of every widget if the gesture ended now. */
  preview: Map<string, Rect>;
}

export function useDragResize(opts: {
  gridRef: RefObject<HTMLElement | null>;
  cols: number;
  items: LayoutItem[];
  onCommit: (a: DragCommit) => void;
}): {
  onHandlePointerDown(id: string): PointerEventHandler;
  onResizePointerDown(id: string): PointerEventHandler;
  drag: DragState | null;
} {
  const [drag, setDrag] = useState<DragState | null>(null);
  const live = useRef(opts);
  live.current = opts;
  const stopRef = useRef<(() => void) | null>(null);
  useEffect(() => () => stopRef.current?.(), []);

  const start = useCallback(
    (id: string, kind: "move" | "resize"): PointerEventHandler =>
      (e) => {
        if (e.button !== 0) return;
        const grid = live.current.gridRef.current;
        const item = live.current.items.find((i) => i.id === id);
        if (!grid || !item) return;
        e.preventDefault();
        e.stopPropagation();
        stopRef.current?.();

        const cols = live.current.cols;
        const box = grid.getBoundingClientRect();
        const rtl = getComputedStyle(grid).direction === "rtl";
        const stepX = (box.width - GAP * (cols - 1)) / cols + GAP;
        const stepY = ROW_H + GAP;
        const startX = e.clientX;
        const startY = e.clientY;
        const handle = e.currentTarget as Element;
        const pointerId = e.pointerId;
        try {
          handle.setPointerCapture?.(pointerId);
        } catch {
          // not supported (jsdom) or the pointer is gone: window listeners still see the events
        }

        let target: Rect = item.rect;
        const update = (clientX: number, clientY: number) => {
          const dx = clientX - startX;
          const dy = clientY - startY;
          const along = rtl ? -dx : dx;
          const items = live.current.items;
          const next =
            kind === "move"
              ? moveItem(
                  items,
                  id,
                  pixelsToCell({ x: item.rect.x * stepX + along, y: item.rect.y * stepY + dy }, { width: box.width, cols }),
                  cols,
                )
              : resizeItem(
                  items,
                  id,
                  {
                    w: Math.max(1, Math.round((item.rect.w * stepX + along) / stepX)),
                    h: Math.max(1, Math.round((item.rect.h * stepY + dy) / stepY)),
                  },
                  cols,
                );
          const preview = new Map(next.map((i) => [i.id, i.rect]));
          target = preview.get(id) ?? item.rect;
          setDrag({ id, kind, ghost: { dx, dy }, target, preview });
        };

        const onMove = (ev: PointerEvent) => update(ev.clientX, ev.clientY);
        const onUp = () => {
          stop();
          const r = item.rect;
          if (kind === "move" && (target.x !== r.x || target.y !== r.y)) {
            live.current.onCommit({ kind: "move", id, to: { x: target.x, y: target.y } });
          } else if (kind === "resize" && (target.w !== r.w || target.h !== r.h)) {
            live.current.onCommit({ kind: "resize", id, size: { w: target.w, h: target.h } });
          }
        };
        const onKey = (ev: KeyboardEvent) => {
          if (ev.key !== "Escape") return;
          ev.preventDefault();
          ev.stopPropagation();
          stop();
        };
        const stop = () => {
          window.removeEventListener("pointermove", onMove);
          window.removeEventListener("pointerup", onUp);
          window.removeEventListener("pointercancel", stop);
          window.removeEventListener("keydown", onKey, true);
          try {
            handle.releasePointerCapture?.(pointerId);
          } catch {
            // already released
          }
          stopRef.current = null;
          setDrag(null);
        };
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp);
        window.addEventListener("pointercancel", stop);
        window.addEventListener("keydown", onKey, true);
        stopRef.current = stop;
        setDrag({ id, kind, ghost: { dx: 0, dy: 0 }, target: item.rect, preview: new Map(live.current.items.map((i) => [i.id, i.rect])) });
      },
    [],
  );

  const onHandlePointerDown = useCallback((id: string) => start(id, "move"), [start]);
  const onResizePointerDown = useCallback((id: string) => start(id, "resize"), [start]);
  return { onHandlePointerDown, onResizePointerDown, drag };
}
