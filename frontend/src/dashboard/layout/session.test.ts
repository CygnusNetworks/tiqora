import { expect, test } from "vitest";
import type { EditWidget, Rect } from "../model";
import { editReducer, initialEditState, type EditAction, type EditState, type WidgetLike } from "./session";

const COUNTER: WidgetLike = {
  key: "core.counter",
  version: 1,
  size: { default: [3, 2], min: [2, 2], max: [4, 3] },
  settingsDefaults: { preset: "mine_open" },
};
const w = (id: string, lg: Rect = { x: 0, y: 0, w: 3, h: 2 }): EditWidget => ({
  id,
  type_key: "core.counter",
  type_version: 1,
  title_override: null,
  settings: {},
  layout: { lg },
});
const w1 = w("w1");
const state = (ws: EditWidget[]): EditState => initialEditState("Start", ws, 3);

test("add places the widget in the first free spot with the default size", () => {
  const s = editReducer(state([]), { type: "add", def: COUNTER, id: "n1", bp: "lg" });
  expect(s.widgets[0].layout.lg).toEqual({ x: 0, y: 0, w: 3, h: 2 });
  expect(s.widgets[0].settings).toEqual({ preset: "mine_open" });
  expect(s.dirty).toBe(true);
  expect(s.past).toHaveLength(1);
});

test("add at a drop cell honours it and pushes colliding widgets", () => {
  const s = editReducer(state([w("a", { x: 0, y: 0, w: 6, h: 2 })]), { type: "add", def: COUNTER, id: "n1", at: { x: 0, y: 0 }, bp: "lg" });
  expect(s.widgets.find((x) => x.id === "n1")!.layout.lg).toMatchObject({ x: 0, y: 0 });
  expect(s.widgets.find((x) => x.id === "a")!.layout.lg.y).toBe(2);
});

test("add at md writes md for all and puts the new widget on lg as well", () => {
  const s = editReducer(state([w1]), { type: "add", def: COUNTER, id: "n1", bp: "md" });
  const n = s.widgets.find((x) => x.id === "n1")!;
  expect(n.layout.md).toEqual({ x: 2, y: 0, w: 3, h: 2 });
  expect(n.layout.lg).toEqual({ x: 3, y: 0, w: 3, h: 2 });
  expect(s.widgets[0].layout.lg).toEqual(w1.layout.lg);
});

test("undo and redo restore exactly, redo is cleared by a new action", () => {
  let s = editReducer(state([w1]), { type: "move", id: "w1", to: { x: 3, y: 0 }, bp: "lg" });
  s = editReducer(s, { type: "undo" });
  expect(s.widgets[0].layout.lg.x).toBe(0);
  expect(s.dirty).toBe(false);
  s = editReducer(s, { type: "redo" });
  expect(s.widgets[0].layout.lg.x).toBe(3);
  s = editReducer(editReducer(s, { type: "undo" }), { type: "remove", id: "w1" });
  expect(s.future).toHaveLength(0);
});

test("undo and redo on empty stacks are no-ops", () => {
  const s = state([w1]);
  expect(editReducer(s, { type: "undo" })).toBe(s);
  expect(editReducer(s, { type: "redo" })).toBe(s);
});

test("keyboard moves within 800 ms coalesce into one undo step, later ones do not", () => {
  let s = state([w1]);
  for (const t of [0, 100, 200]) s = editReducer(s, { type: "move", id: "w1", to: { x: t / 100 + 1, y: 0 }, bp: "lg", now: t, coalesce: true });
  expect(s.past).toHaveLength(1);
  expect(s.widgets[0].layout.lg.x).toBe(3);
  s = editReducer(s, { type: "move", id: "w1", to: { x: 9, y: 0 }, bp: "lg", now: 5000, coalesce: true });
  expect(s.past).toHaveLength(2);
  // undoing the coalesced step returns to the state before the first move
  expect(editReducer(editReducer(s, { type: "undo" }), { type: "undo" }).widgets[0].layout.lg.x).toBe(0);
});

test("an action in between ends the coalescing", () => {
  let s = editReducer(state([w1]), { type: "move", id: "w1", to: { x: 1, y: 0 }, bp: "lg", now: 0, coalesce: true });
  s = editReducer(s, { type: "rename", name: "X" });
  s = editReducer(s, { type: "move", id: "w1", to: { x: 2, y: 0 }, bp: "lg", now: 100, coalesce: true });
  expect(s.past).toHaveLength(3);
});

test("the undo stack is capped at 100", () => {
  let s = state([w1]);
  for (let i = 0; i < 120; i++) s = editReducer(s, { type: "rename", name: `n${i}` });
  expect(s.past).toHaveLength(100);
  expect(s.past[0].name).toBe("n19");
});

test("select never creates history; settings, rename, duplicate and remove each create exactly one", () => {
  let s = editReducer(state([w1]), { type: "select", id: "w1" });
  expect(s.past).toHaveLength(0);
  expect(s.dirty).toBe(false);
  const actions: EditAction[] = [
    { type: "settings", id: "w1", settings: { limit: 3 }, titleOverride: "X" },
    { type: "rename", name: "N" },
    { type: "duplicate", id: "w1", newId: "w2" },
    { type: "remove", id: "w2" },
  ];
  for (const [i, a] of actions.entries()) {
    s = editReducer(s, a);
    expect(s.past).toHaveLength(i + 1);
  }
  expect(s.widgets[0]).toMatchObject({ settings: { limit: 3 }, title_override: "X" });
  expect(s.name).toBe("N");
});

test("duplicate gets the new id, same settings, first free spot, and a deep-copied settings object", () => {
  const src = { ...w1, settings: { columns: ["tn"] } };
  const dup = editReducer(state([src]), { type: "duplicate", id: "w1", newId: "w9" }).widgets.find((x) => x.id === "w9")!;
  expect(dup.settings).toEqual({ columns: ["tn"] });
  expect(dup.settings).not.toBe(src.settings);
  expect((dup.settings.columns as string[])).not.toBe(src.settings.columns);
  expect(dup.layout.lg).not.toEqual(src.layout.lg);
});

test("editing at md writes layout.md and leaves lg untouched", () => {
  const s = editReducer(state([w1]), { type: "move", id: "w1", to: { x: 4, y: 3 }, bp: "md" });
  expect(s.widgets[0].layout.md).toMatchObject({ x: 4, y: 3 });
  expect(s.widgets[0].layout.lg).toEqual(w1.layout.lg);
});

test("editing at lg keeps an explicit md rect", () => {
  const withMd = { ...w1, layout: { lg: w1.layout.lg, md: { x: 5, y: 1, w: 3, h: 2 } } };
  const s = editReducer(state([withMd]), { type: "move", id: "w1", to: { x: 9, y: 0 }, bp: "lg" });
  expect(s.widgets[0].layout.md).toEqual({ x: 5, y: 1, w: 3, h: 2 });
});

test("resize honours the min and max passed with the action and pushes neighbours", () => {
  const s = editReducer(state([w("a"), w("b", { x: 0, y: 2, w: 3, h: 2 })]), {
    type: "resize", id: "a", size: { w: 20, h: 9 }, bp: "lg", min: [2, 2], max: [4, 3],
  });
  expect(s.widgets[0].layout.lg).toEqual({ x: 0, y: 0, w: 4, h: 3 });
  expect(s.widgets[1].layout.lg.y).toBe(3);
});

test("shift moves a widget one place in phone order and is a no-op at the ends", () => {
  const s = editReducer(state([w("a"), w("b"), w("c")]), { type: "shift", id: "b", dir: "up" });
  expect(s.widgets.map((x) => x.id)).toEqual(["b", "a", "c"]);
  const top = editReducer(s, { type: "shift", id: "b", dir: "up" });
  expect(top.widgets.map((x) => x.id)).toEqual(["b", "a", "c"]);
  expect(top.past).toHaveLength(s.past.length);
  const down = editReducer(s, { type: "shift", id: "c", dir: "down" });
  expect(down.past).toHaveLength(s.past.length);
});

test("reset replaces everything, clears history and sets baseVersion", () => {
  const s = editReducer(editReducer(state([w1]), { type: "rename", name: "X" }), { type: "reset", name: "Neu", widgets: [w("z")], version: 7 });
  expect(s).toMatchObject({ name: "Neu", baseVersion: 7, dirty: false, past: [], future: [] });
  expect(s.widgets.map((x) => x.id)).toEqual(["z"]);
});

test("the reducer does not mutate its input state", () => {
  const s0 = state([w("a"), w("b", { x: 0, y: 2, w: 3, h: 2 })]);
  const before = structuredClone(s0);
  for (const a of [
    { type: "move", id: "b", to: { x: 0, y: 0 }, bp: "lg" },
    { type: "duplicate", id: "a", newId: "c" },
    { type: "shift", id: "a", dir: "down" },
    { type: "add", def: COUNTER, id: "n", bp: "md" },
  ] as EditAction[]) editReducer(s0, a);
  expect(s0).toEqual(before);
});

test("removing the selected widget clears the selection; undo of a remove brings it back", () => {
  let s = editReducer(state([w1]), { type: "select", id: "w1" });
  s = editReducer(s, { type: "remove", id: "w1" });
  expect(s.selectedId).toBeNull();
  expect(editReducer(s, { type: "undo" }).widgets.map((x) => x.id)).toEqual(["w1"]);
});
