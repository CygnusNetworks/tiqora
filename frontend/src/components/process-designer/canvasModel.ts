// Pure model behind the process designer canvas: draft <-> nodes/edges plus the edits that touch the
// process shape (Path, Layout, start). No React, no I/O; every edit returns a new shape.
import type { ProcessDraft, ValidationIssue } from "@/lib/processDesignerApi";
import type { Schemas } from "@tiqora/api-client";

type ProcessUpdateIn = Schemas["ProcessUpdateIn"];

export type CanvasNode = {
  id: string; // activity entity id
  position: { x: number; y: number };
  data: {
    name: string;
    dialogs: { id: string; name: string }[];
    isStart: boolean;
    isEnd: boolean;
    broken: boolean;
    issues: number;
  };
};

export type CanvasEdge = {
  id: string; // `${from}|${transitionEid}`
  source: string;
  target: string;
  data: {
    transitionId: string;
    name: string;
    actions: { id: string; name: string }[];
    issues: number;
  };
};

export type ProcessShape = {
  path: ProcessUpdateIn["path"];
  layout: ProcessUpdateIn["layout"];
  startActivity: string;
  startActivityDialog: string;
};

type Point = { x: number; y: number };

const COLUMN_WIDTH = 260;
const ROW_HEIGHT = 140;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function targetOf(def: Record<string, unknown> | undefined): string {
  const t = def?.ActivityEntityID;
  return typeof t === "string" ? t : "";
}

function actionIdsOf(def: Record<string, unknown> | undefined): string[] {
  const a = def?.TransitionAction;
  return Array.isArray(a) ? a.filter((x): x is string => typeof x === "string") : [];
}

export function shapeOf(draft: ProcessDraft): ProcessShape {
  const cfg = draft.process.config as Record<string, unknown>;
  return {
    path: clone((cfg.Path ?? {}) as ProcessShape["path"]),
    layout: clone(draft.layout ?? {}) as ProcessShape["layout"],
    startActivity: typeof cfg.StartActivity === "string" ? cfg.StartActivity : "",
    startActivityDialog: typeof cfg.StartActivityDialog === "string" ? cfg.StartActivityDialog : "",
  };
}

function layoutPoint(shape: ProcessShape, id: string): Point | null {
  const entry = shape.layout[id];
  if (!entry) return null;
  const x = Number(entry.left);
  const y = Number(entry.top);
  return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
}

export function autoLayout(shape: ProcessShape): ProcessShape {
  const next = clone(shape);
  const ids = Object.keys(next.path);
  const missing = ids.filter((id) => !layoutPoint(next, id));
  if (missing.length === 0) return next;

  // BFS depth from the start activity; unreachable activities seed further BFS runs.
  const depth = new Map<string, number>();
  const seeds = [next.startActivity, ...ids].filter((id) => id in next.path);
  for (const seed of seeds) {
    if (depth.has(seed)) continue;
    depth.set(seed, seed === next.startActivity ? 0 : Math.max(0, ...depth.values()) + (depth.size ? 1 : 0));
    const queue = [seed];
    while (queue.length) {
      const cur = queue.shift() as string;
      for (const def of Object.values(next.path[cur] ?? {})) {
        const t = targetOf(def);
        if (t && t in next.path && !depth.has(t)) {
          depth.set(t, (depth.get(cur) as number) + 1);
          queue.push(t);
        }
      }
    }
  }
  const rows = new Map<number, number>();
  for (const id of ids) {
    const col = depth.get(id) ?? 0;
    if (layoutPoint(next, id)) rows.set(col, Math.max(rows.get(col) ?? 0, 0) + 1);
  }
  for (const id of missing) {
    const col = depth.get(id) ?? 0;
    const row = rows.get(col) ?? 0;
    rows.set(col, row + 1);
    next.layout[id] = { left: String(40 + col * COLUMN_WIDTH), top: String(40 + row * ROW_HEIGHT) };
  }
  return next;
}

export function toCanvas(
  draft: ProcessDraft,
  issues: ValidationIssue[] = [],
): { nodes: CanvasNode[]; edges: CanvasEdge[] } {
  const shape = autoLayout(shapeOf(draft));
  const activities = new Map(draft.activities.map((a) => [a.entity_id, a]));
  const dialogs = new Map(draft.activity_dialogs.map((d) => [d.entity_id, d.name]));
  const transitions = new Map(draft.transitions.map((t) => [t.entity_id, t.name]));
  const actions = new Map(draft.transition_actions.map((a) => [a.entity_id, a.name]));
  const missing = new Set(draft.missing_references);

  const countIssues = (type: string, id: string) =>
    issues.filter((i) => i.entity_type === type && i.entity_id === id).length;

  const nodeIds = new Set(Object.keys(shape.path));
  const edges: CanvasEdge[] = [];
  for (const [from, outgoing] of Object.entries(shape.path)) {
    for (const [tid, def] of Object.entries(outgoing ?? {})) {
      const to = targetOf(def);
      if (!to) continue;
      nodeIds.add(to);
      edges.push({
        id: `${from}|${tid}`,
        source: from,
        target: to,
        data: {
          transitionId: tid,
          name: transitions.get(tid) ?? tid,
          actions: actionIdsOf(def).map((id) => ({ id, name: actions.get(id) ?? id })),
          issues: countIssues("Transition", tid),
        },
      });
    }
  }

  const nodes: CanvasNode[] = [...nodeIds].map((id) => {
    const activity = activities.get(id);
    const dialogCfg = (activity?.config as { ActivityDialog?: Record<string, string> } | undefined)?.ActivityDialog ?? {};
    const dialogList = Object.keys(dialogCfg)
      .sort((a, b) => Number(a) - Number(b))
      .map((k) => ({ id: dialogCfg[k], name: dialogs.get(dialogCfg[k]) ?? dialogCfg[k] }));
    const p = layoutPoint(shape, id) ?? { x: 40, y: 40 };
    return {
      id,
      position: p,
      data: {
        name: activity?.name ?? id,
        dialogs: dialogList,
        isStart: id === shape.startActivity,
        isEnd: Object.keys(shape.path[id] ?? {}).length === 0,
        broken: !activity || missing.has(id),
        issues: countIssues("Activity", id),
      },
    };
  });
  return { nodes, edges };
}

export function addActivity(shape: ProcessShape, activityId: string, at: Point): ProcessShape {
  const next = clone(shape);
  if (!(activityId in next.path)) next.path[activityId] = {};
  return move(next, activityId, at);
}

export function removeActivity(shape: ProcessShape, activityId: string): ProcessShape {
  const next = clone(shape);
  delete next.path[activityId];
  delete next.layout[activityId];
  for (const outgoing of Object.values(next.path)) {
    for (const [tid, def] of Object.entries(outgoing)) {
      if (targetOf(def) === activityId) delete outgoing[tid];
    }
  }
  if (next.startActivity === activityId) {
    next.startActivity = "";
    next.startActivityDialog = "";
  }
  return next;
}

export function connect(shape: ProcessShape, from: string, transitionId: string, to: string): ProcessShape {
  const next = clone(shape);
  next.path[from] = next.path[from] ?? {};
  if (!(to in next.path)) next.path[to] = {};
  const existing = next.path[from][transitionId] ?? {};
  next.path[from][transitionId] = { ...existing, ActivityEntityID: to, TransitionAction: actionIdsOf(existing) };
  return next;
}

export function retarget(shape: ProcessShape, from: string, transitionId: string, to: string): ProcessShape {
  const next = clone(shape);
  const def = next.path[from]?.[transitionId];
  if (!def) return next;
  def.ActivityEntityID = to;
  if (!(to in next.path)) next.path[to] = {};
  return next;
}

export function disconnect(shape: ProcessShape, from: string, transitionId: string): ProcessShape {
  const next = clone(shape);
  if (next.path[from]) delete next.path[from][transitionId];
  return next;
}

export function setActions(shape: ProcessShape, from: string, transitionId: string, actionIds: string[]): ProcessShape {
  const next = clone(shape);
  const def = next.path[from]?.[transitionId];
  if (def) def.TransitionAction = [...actionIds];
  return next;
}

export function move(shape: ProcessShape, activityId: string, at: Point): ProcessShape {
  const next = clone(shape);
  next.layout[activityId] = { left: String(Math.round(at.x)), top: String(Math.round(at.y)) };
  return next;
}

export function setStart(shape: ProcessShape, activityId: string, dialogId: string): ProcessShape {
  const next = clone(shape);
  next.startActivity = activityId;
  next.startActivityDialog = dialogId;
  return next;
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([k, v]) => [k, canonical(v)]),
    );
  }
  return value;
}

function normalised(shape: ProcessShape): string {
  const layout = Object.fromEntries(
    Object.entries(shape.layout).map(([id, pos]) => [
      id,
      Object.fromEntries(Object.entries(pos).map(([k, v]) => [k, String(v)])),
    ]),
  );
  return JSON.stringify(canonical({ ...shape, layout }));
}

export function isDirty(a: ProcessShape, b: ProcessShape): boolean {
  return normalised(a) !== normalised(b);
}
