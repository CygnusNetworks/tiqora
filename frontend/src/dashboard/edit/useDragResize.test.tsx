import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { useRef } from "react";
import type { LayoutItem } from "../model";
import { useDragResize, type DragCommit } from "./useDragResize";

/** jsdom has no PointerEvent: a MouseEvent of the pointer type carries clientX/Y to React and to window listeners. */
function firePointer(target: EventTarget, type: string, init: { clientX?: number; clientY?: number } = {}) {
  const ev = new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientX: init.clientX ?? 0, clientY: init.clientY ?? 0 });
  Object.defineProperty(ev, "pointerId", { value: 1 });
  act(() => {
    target.dispatchEvent(ev);
  });
}

// 1200 px wide, 12 columns: one column step = (1200 - 11 * 16) / 12 + 16 = 101.33 px, one row step = 64 + 16 = 80 px
const STEP_X = (1200 - 11 * 16) / 12 + 16;
const STEP_Y = 80;

const item = (id: string, x: number, y: number, w: number, h: number, min: [number, number] = [1, 1], max: [number, number] = [12, 24]): LayoutItem => ({
  id,
  rect: { x, y, w, h },
  min,
  max,
});
const ITEMS: LayoutItem[] = [item("a", 0, 0, 6, 2), item("b", 6, 0, 6, 2), item("c", 0, 2, 12, 3), item("k", 0, 5, 3, 2, [2, 2], [4, 3])];

function Harness({ items = ITEMS, onCommit, dir = "ltr" }: { items?: LayoutItem[]; onCommit: (c: DragCommit) => void; dir?: "ltr" | "rtl" }) {
  const ref = useRef<HTMLDivElement>(null);
  const { onHandlePointerDown, onResizePointerDown, drag } = useDragResize({ gridRef: ref, cols: 12, items, onCommit });
  return (
    <div ref={ref} dir={dir} style={{ direction: dir }} data-testid="grid">
      {items.map((i) => (
        <div key={i.id}>
          <span data-testid={`${i.id}-handle`} onPointerDown={onHandlePointerDown(i.id)} />
          <span data-testid={`${i.id}-resize`} onPointerDown={onResizePointerDown(i.id)} />
        </div>
      ))}
      <output data-testid="drag">
        {drag ? JSON.stringify({ ...drag, preview: Object.fromEntries(drag.preview) }) : ""}
      </output>
    </div>
  );
}

const dragState = () => {
  const raw = screen.getByTestId("drag").textContent;
  return raw ? JSON.parse(raw) : null;
};

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0, top: 0, right: 1200, bottom: 2000, width: 1200, height: 2000, x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("a move gesture shows the snapped target and the live layout, and commits once on pointerup", () => {
  const onCommit = vi.fn();
  render(<Harness onCommit={onCommit} />);
  firePointer(screen.getByTestId("c-handle"), "pointerdown", { clientX: 500, clientY: 200 });
  expect(dragState()).toMatchObject({ id: "c", kind: "move", ghost: { dx: 0, dy: 0 } });
  // up two rows: c lands at y 0 and pushes a and b below it
  firePointer(window, "pointermove", { clientX: 500 + 10, clientY: 200 - 2 * STEP_Y - 15 });
  const d = dragState();
  expect(d.ghost).toEqual({ dx: 10, dy: -175 });
  expect(d.target).toEqual({ x: 0, y: 0, w: 12, h: 3 });
  expect(d.preview.a).toEqual({ x: 0, y: 3, w: 6, h: 2 });
  expect(d.preview.b).toEqual({ x: 6, y: 3, w: 6, h: 2 });
  expect(onCommit).not.toHaveBeenCalled();
  firePointer(window, "pointerup");
  expect(onCommit).toHaveBeenCalledTimes(1);
  expect(onCommit).toHaveBeenCalledWith({ kind: "move", id: "c", to: { x: 0, y: 0 } });
  expect(dragState()).toBeNull();
});

test("the target is clamped into the grid", () => {
  const onCommit = vi.fn();
  render(<Harness onCommit={onCommit} />);
  firePointer(screen.getByTestId("a-handle"), "pointerdown", { clientX: 10, clientY: 10 });
  firePointer(window, "pointermove", { clientX: 10 + 9 * STEP_X, clientY: 10 });
  expect(dragState().target).toMatchObject({ x: 6, y: 0 });
  firePointer(window, "pointerup");
  expect(onCommit).toHaveBeenCalledWith({ kind: "move", id: "a", to: { x: 6, y: 0 } });
});

test("a gesture that ends where it started commits nothing", () => {
  const onCommit = vi.fn();
  render(<Harness onCommit={onCommit} />);
  firePointer(screen.getByTestId("a-handle"), "pointerdown", { clientX: 10, clientY: 10 });
  firePointer(window, "pointermove", { clientX: 30, clientY: 20 });
  firePointer(window, "pointerup");
  expect(onCommit).not.toHaveBeenCalled();
});

test("Escape and pointercancel abort without a commit", () => {
  const onCommit = vi.fn();
  render(<Harness onCommit={onCommit} />);
  firePointer(screen.getByTestId("a-handle"), "pointerdown", { clientX: 10, clientY: 10 });
  firePointer(window, "pointermove", { clientX: 300, clientY: 300 });
  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  expect(dragState()).toBeNull();
  firePointer(window, "pointerup");
  firePointer(screen.getByTestId("b-handle"), "pointerdown", { clientX: 10, clientY: 10 });
  firePointer(window, "pointermove", { clientX: 300, clientY: 300 });
  firePointer(window, "pointercancel");
  expect(dragState()).toBeNull();
  expect(onCommit).not.toHaveBeenCalled();
});

test("resizing snaps to cells, respects min and max and pushes the widgets below", () => {
  const onCommit = vi.fn();
  render(<Harness onCommit={onCommit} />);
  firePointer(screen.getByTestId("k-resize"), "pointerdown", { clientX: 300, clientY: 560 });
  firePointer(window, "pointermove", { clientX: 300 + 6 * STEP_X, clientY: 560 + 5 * STEP_Y });
  expect(dragState()).toMatchObject({ id: "k", kind: "resize", target: { x: 0, y: 5, w: 4, h: 3 } });
  firePointer(window, "pointerup");
  expect(onCommit).toHaveBeenCalledWith({ kind: "resize", id: "k", size: { w: 4, h: 3 } });

  onCommit.mockClear();
  firePointer(screen.getByTestId("c-resize"), "pointerdown", { clientX: 1200, clientY: 400 });
  firePointer(window, "pointermove", { clientX: 1200, clientY: 400 + 2 * STEP_Y });
  const d = dragState();
  expect(d.target).toEqual({ x: 0, y: 2, w: 12, h: 5 });
  expect(d.preview.k.y).toBe(7);
  firePointer(window, "pointerup");
  expect(onCommit).toHaveBeenCalledWith({ kind: "resize", id: "c", size: { w: 12, h: 5 } });
});

test("right-to-left grids flip the horizontal direction", () => {
  const onCommit = vi.fn();
  render(<Harness onCommit={onCommit} dir="rtl" />);
  firePointer(screen.getByTestId("b-handle"), "pointerdown", { clientX: 600, clientY: 10 });
  // dragging to the screen-right in RTL moves towards the inline start (lower x)
  firePointer(window, "pointermove", { clientX: 600 + 3 * STEP_X, clientY: 10 });
  firePointer(window, "pointerup");
  expect(onCommit).toHaveBeenCalledWith({ kind: "move", id: "b", to: { x: 3, y: 0 } });
  onCommit.mockClear();
  firePointer(screen.getByTestId("a-handle"), "pointerdown", { clientX: 600, clientY: 10 });
  firePointer(window, "pointermove", { clientX: 600 - 3 * STEP_X, clientY: 10 });
  firePointer(window, "pointerup");
  expect(onCommit).toHaveBeenCalledWith({ kind: "move", id: "a", to: { x: 3, y: 0 } });
});

test("unmounting mid-gesture removes the window listeners", () => {
  const onCommit = vi.fn();
  const view = render(<Harness onCommit={onCommit} />);
  firePointer(screen.getByTestId("a-handle"), "pointerdown", { clientX: 10, clientY: 10 });
  view.unmount();
  firePointer(window, "pointermove", { clientX: 400, clientY: 10 });
  firePointer(window, "pointerup");
  expect(onCommit).not.toHaveBeenCalled();
});
