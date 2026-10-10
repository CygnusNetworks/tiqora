import { COLS, MAX_H, type EditWidget, type LayoutItem } from "../model";
import { layoutForBreakpoint } from "./breakpoints";
import { clampRect, compactUp, firstFreeSpot, moveItem, resizeItem, resolveCollisions } from "./grid";

export interface WidgetLike {
  key: string;
  version: number;
  size: { default: [number, number]; min: [number, number]; max: [number, number] };
  settingsDefaults: Record<string, unknown>;
}

export interface Snapshot {
  name: string;
  widgets: EditWidget[];
}

export interface EditState {
  name: string;
  widgets: EditWidget[];
  selectedId: string | null;
  past: Snapshot[];
  future: Snapshot[];
  lastCoalesce: { key: string; at: number } | null;
  baseVersion: number;
  dirty: boolean;
}

type Bp = "lg" | "md";
type Size = [number, number];

/** Injected size limits per widget type (Task 12 passes the widget registry's sizes). Unknown type -> undefined. */
export type SizeLookup = (typeKey: string) => { min: Size; max?: Size } | undefined;
const noSizes: SizeLookup = () => undefined;

export type EditAction =
  | { type: "reset"; name: string; widgets: EditWidget[]; version: number }
  | { type: "add"; def: WidgetLike; id: string; at?: { x: number; y: number }; bp: Bp }
  | { type: "move"; id: string; to: { x: number; y: number }; bp: Bp; now?: number; coalesce?: boolean }
  | { type: "resize"; id: string; size: { w: number; h: number }; bp: Bp; now?: number; coalesce?: boolean }
  | { type: "remove"; id: string }
  | { type: "duplicate"; id: string; newId: string }
  | { type: "settings"; id: string; settings: Record<string, unknown>; titleOverride: string | null }
  | { type: "rename"; name: string }
  | { type: "shift"; id: string; dir: "up" | "down" }
  | { type: "select"; id: string | null }
  | { type: "undo" }
  | { type: "redo" };

export const HISTORY_LIMIT = 100;
export const COALESCE_MS = 800;

export function initialEditState(name: string, widgets: EditWidget[], version: number): EditState {
  return { name, widgets, selectedId: null, past: [], future: [], lastCoalesce: null, baseVersion: version, dirty: false };
}

const snap = (s: EditState): Snapshot => ({ name: s.name, widgets: s.widgets });

/** Record the current state for undo, clear redo. */
function commit(s: EditState, patch: Partial<EditState>, coalesce: { key: string; at: number } | null = null): EditState {
  return {
    ...s,
    ...patch,
    past: [...s.past, snap(s)].slice(-HISTORY_LIMIT),
    future: [],
    lastCoalesce: coalesce,
    dirty: true,
  };
}

function limits(sizes: SizeLookup, key: string, cols: number): { min: Size; max: Size } {
  const l = sizes(key);
  const min = l?.min ?? [1, 1];
  const max = l?.max ?? [cols, MAX_H];
  return { min: [Math.min(min[0], cols), min[1]], max: [Math.min(max[0], cols), max[1]] };
}

function itemsAt(widgets: EditWidget[], bp: Bp, sizes: SizeLookup): LayoutItem[] {
  const cols = COLS[bp];
  const rects = layoutForBreakpoint(widgets, bp, (k) => limits(sizes, k, cols).min);
  return widgets.map((w) => ({ id: w.id, rect: rects.get(w.id)!, ...limits(sizes, w.type_key, cols) }));
}

/** Write the rects of `items` back into `layout[bp]` of every widget (md becomes explicit for all). */
function applyItems(widgets: EditWidget[], bp: Bp, items: LayoutItem[]): EditWidget[] {
  const byId = new Map(items.map((i) => [i.id, i.rect]));
  return widgets.map((w) => {
    const r = byId.get(w.id);
    if (!r) return w;
    return { ...w, layout: bp === "lg" ? { ...w.layout, lg: { ...r } } : { ...w.layout, md: { ...r } } };
  });
}

function gesture(s: EditState, a: Extract<EditAction, { type: "move" | "resize" }>, sizes: SizeLookup): EditState {
  const cols = COLS[a.bp];
  const items = itemsAt(s.widgets, a.bp, sizes);
  const idx = items.findIndex((i) => i.id === a.id);
  if (idx < 0) return s;
  const next =
    a.type === "move" ? moveItem(items, a.id, a.to, cols) : resizeItem(items, a.id, a.size, cols);
  if (next.every((n, i) => JSON.stringify(n.rect) === JSON.stringify(items[i].rect))) {
    return s; // no-op gesture: no history step
  }
  const widgets = applyItems(s.widgets, a.bp, next);
  const key = `${a.type}:${a.id}:${a.bp}`;
  if (a.coalesce && a.now !== undefined) {
    const same = s.lastCoalesce && s.lastCoalesce.key === key && a.now - s.lastCoalesce.at < COALESCE_MS;
    if (same) return { ...s, widgets, lastCoalesce: { key, at: a.now }, dirty: true };
    return commit(s, { widgets }, { key, at: a.now });
  }
  return commit(s, { widgets });
}

function restore(s: EditState, from: Snapshot, past: Snapshot[], future: Snapshot[]): EditState {
  return {
    ...s,
    name: from.name,
    widgets: from.widgets,
    selectedId: from.widgets.some((w) => w.id === s.selectedId) ? s.selectedId : null,
    past,
    future,
    lastCoalesce: null,
    dirty: true,
  };
}

/** Pure reducer with injected per-type size limits (min/max); unknown types fall back to [1,1]. */
export function createEditReducer(sizes: SizeLookup): (s: EditState, a: EditAction) => EditState {
  return (s, a) => reduce(s, a, sizes);
}

/** Reducer without size knowledge (every type is [1,1]..[cols,24]). Prefer `createEditReducer`. */
export const editReducer = createEditReducer(noSizes);

function reduce(s: EditState, a: EditAction, sizes: SizeLookup): EditState {
  switch (a.type) {
    case "reset":
      return initialEditState(a.name, a.widgets, a.version);
    case "select":
      return a.id === s.selectedId ? s : { ...s, selectedId: a.id };
    case "move":
    case "resize":
      return gesture(s, a, sizes);
    case "add": {
      const [w, h] = a.def.size.default;
      const cols = COLS[a.bp];
      const items = itemsAt(s.widgets, a.bp, sizes);
      const spot = a.at ?? firstFreeSpot(items, w, h, cols);
      const rect = clampRect({ ...spot, w, h }, cols, a.def.size.min, a.def.size.max);
      const fresh: LayoutItem = { id: a.id, rect, min: a.def.size.min, max: a.def.size.max };
      const placed = compactUp(resolveCollisions([...items, fresh], a.id, cols), cols, a.id);
      let widget: EditWidget = {
        id: a.id,
        type_key: a.def.key,
        type_version: a.def.version,
        title_override: null,
        settings: structuredClone(a.def.settingsDefaults),
        layout: { lg: rect },
      };
      let widgets = [...s.widgets, widget];
      if (a.bp === "md") {
        // md edit: the lg rect of the new widget goes to the first free lg spot
        const lg = firstFreeSpot(itemsAt(s.widgets, "lg", sizes), w, h, COLS.lg);
        widget = { ...widget, layout: { lg: { ...lg, w, h }, md: rect } };
        widgets = [...s.widgets, widget];
      }
      return commit(s, { widgets: applyItems(widgets, a.bp, placed), selectedId: a.id });
    }
    case "remove": {
      if (!s.widgets.some((w) => w.id === a.id)) return s;
      return commit(s, { widgets: s.widgets.filter((w) => w.id !== a.id), selectedId: s.selectedId === a.id ? null : s.selectedId });
    }
    case "duplicate": {
      const idx = s.widgets.findIndex((w) => w.id === a.id);
      if (idx < 0) return s;
      const src = s.widgets[idx];
      const lgItems = itemsAt(s.widgets, "lg", sizes);
      const spot = firstFreeSpot(lgItems, src.layout.lg.w, src.layout.lg.h, COLS.lg);
      const copy: EditWidget = {
        ...src,
        id: a.newId,
        settings: structuredClone(src.settings),
        layout: { lg: { ...src.layout.lg, ...spot } },
      };
      const widgets = [...s.widgets.slice(0, idx + 1), copy, ...s.widgets.slice(idx + 1)];
      return commit(s, { widgets, selectedId: a.newId });
    }
    case "settings": {
      if (!s.widgets.some((w) => w.id === a.id)) return s;
      return commit(s, {
        widgets: s.widgets.map((w) => (w.id === a.id ? { ...w, settings: structuredClone(a.settings), title_override: a.titleOverride } : w)),
      });
    }
    case "rename":
      return commit(s, { name: a.name });
    case "shift": {
      const i = s.widgets.findIndex((w) => w.id === a.id);
      const j = a.dir === "up" ? i - 1 : i + 1;
      if (i < 0 || j < 0 || j >= s.widgets.length) return s;
      const widgets = [...s.widgets];
      [widgets[i], widgets[j]] = [widgets[j], widgets[i]];
      return commit(s, { widgets });
    }
    case "undo": {
      const prev = s.past[s.past.length - 1];
      if (!prev) return s;
      return { ...restore(s, prev, s.past.slice(0, -1), [...s.future, snap(s)]), dirty: s.past.length > 1 };
    }
    case "redo": {
      const next = s.future[s.future.length - 1];
      if (!next) return s;
      return restore(s, next, [...s.past, snap(s)].slice(-HISTORY_LIMIT), s.future.slice(0, -1));
    }
  }
}

