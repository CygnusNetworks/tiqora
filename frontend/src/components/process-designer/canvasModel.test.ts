import { describe, expect, it } from "vitest";
import type { ProcessDraft, ValidationIssue } from "@/lib/processDesignerApi";
import {
  addActivity, autoLayout, connect, disconnect, isDirty, move, removeActivity, retarget, setActions, setStart,
  shapeOf, toCanvas, type ProcessShape,
} from "./canvasModel";

const ent = (entity_type: string, entity_id: string, name: string, config: Record<string, unknown>) =>
  ({ entity_type, entity_id, id: 1, name, config, version: "v", sync_state: "synced", changed_at: "2026-10-09T09:00:00Z", changed_by: 1 });

function sample(): ProcessDraft {
  return {
    process: ent("Process", "P1", "Bestellung", {
      StartActivity: "A1", StartActivityDialog: "D1",
      Path: {
        A1: { T1: { ActivityEntityID: "A2", TransitionAction: ["TA1"] } },
        A2: { T2: { ActivityEntityID: "A3" }, T3: { ActivityEntityID: "A1" } },
        A3: {},
      },
    }),
    state_entity_id: "S1", state_name: "Active",
    layout: { A1: { left: "40", top: "60" }, A2: { left: "300", top: "60" }, A3: { left: "560", top: "60" } },
    activities: [
      ent("Activity", "A1", "Erfassen", { ActivityDialog: { "2": "D2", "1": "D1" } }),
      ent("Activity", "A2", "Prüfen", { ActivityDialog: {} }),
      ent("Activity", "A3", "Fertig", { ActivityDialog: {} }),
    ],
    activity_dialogs: [ent("ActivityDialog", "D1", "Antrag", {}), ent("ActivityDialog", "D2", "Notiz", {})],
    transitions: [ent("Transition", "T1", "vollständig", {}), ent("Transition", "T2", "ok", {})],
    transition_actions: [ent("TransitionAction", "TA1", "Status", {})],
    missing_references: [], pending_changes: 0,
  } as unknown as ProcessDraft;
}

describe("canvasModel", () => {
  it("toCanvas: one node per Path key, edges from Path[from][transition].ActivityEntityID", () => {
    const { nodes, edges } = toCanvas(sample());
    expect(nodes.map((n) => n.id)).toEqual(["A1", "A2", "A3"]);
    expect(edges.map((e) => [e.id, e.source, e.target])).toEqual([
      ["A1|T1", "A1", "A2"], ["A2|T2", "A2", "A3"], ["A2|T3", "A2", "A1"],
    ]);
    expect(edges[0].data).toMatchObject({ transitionId: "T1", name: "vollständig", actions: [{ id: "TA1", name: "Status" }] });
    expect(nodes[1].position).toEqual({ x: 300, y: 60 });
    expect(nodes[0].data.dialogs).toEqual([{ id: "D1", name: "Antrag" }, { id: "D2", name: "Notiz" }]);
  });

  it("start marker on StartActivity; end = no outgoing transitions", () => {
    const { nodes } = toCanvas(sample());
    const by = Object.fromEntries(nodes.map((n) => [n.id, n.data]));
    expect([by.A1.isStart, by.A2.isStart, by.A3.isStart]).toEqual([true, false, false]);
    expect([by.A1.isEnd, by.A2.isEnd, by.A3.isEnd]).toEqual([false, false, true]);
  });

  it("broken references flagged", () => {
    const d = sample();
    d.activities = d.activities.filter((a) => a.entity_id !== "A3");
    d.process.config = { ...d.process.config, Path: { ...(d.process.config.Path as object), A2: { T2: { ActivityEntityID: "A9" } } } };
    const issues = [{ entity_type: "Activity", entity_id: "A1", code: "x", severity: "error", field: "", params: {} }] as ValidationIssue[];
    const { nodes } = toCanvas(d, issues);
    const by = Object.fromEntries(nodes.map((n) => [n.id, n.data]));
    expect(by.A3.broken).toBe(true);
    expect(by.A9.broken).toBe(true);
    expect(by.A1.broken).toBe(false);
    expect(by.A1.issues).toBe(1);
  });

  it("connect / retarget / disconnect / setActions keep other edges untouched", () => {
    const s0 = shapeOf(sample());
    const s1 = connect(s0, "A3", "T4", "A1");
    expect(s1.path.A3.T4).toMatchObject({ ActivityEntityID: "A1" });
    expect(s1.path.A1).toEqual(s0.path.A1);
    expect(s1.path.A2).toEqual(s0.path.A2);
    const s2 = retarget(s1, "A2", "T2", "A1");
    expect(s2.path.A2.T2.ActivityEntityID).toBe("A1");
    expect(s2.path.A2.T3).toEqual(s0.path.A2.T3);
    expect(s2.path.A1).toEqual(s0.path.A1);
    const s3 = setActions(s2, "A1", "T1", ["TA1", "TA2"]);
    expect(s3.path.A1.T1).toEqual({ ActivityEntityID: "A2", TransitionAction: ["TA1", "TA2"] });
    expect(s3.path.A2).toEqual(s2.path.A2);
    const s4 = disconnect(s3, "A2", "T3");
    expect(Object.keys(s4.path.A2)).toEqual(["T2"]);
    expect(s4.path.A1).toEqual(s3.path.A1);
    expect(s0.path.A3).toEqual({}); // input untouched
  });

  it("removeActivity drops incoming and outgoing edges and the layout entry; clears start when needed", () => {
    const s0 = shapeOf(sample());
    const s1 = removeActivity(s0, "A3");
    expect(s1.path.A3).toBeUndefined();
    expect(s1.layout.A3).toBeUndefined();
    expect(Object.keys(s1.path.A2)).toEqual(["T3"]);
    expect(s1.startActivity).toBe("A1");
    const s2 = removeActivity(s0, "A1");
    expect(s2.path.A1).toBeUndefined();
    expect(Object.keys(s2.path.A2)).toEqual(["T2"]);
    expect(s2.startActivity).toBe("");
    expect(s2.startActivityDialog).toBe("");
  });

  it("move stores integer strings; autoLayout fills only missing positions", () => {
    const s0 = shapeOf(sample());
    expect(move(s0, "A1", { x: 10.6, y: 20.2 }).layout.A1).toEqual({ left: "11", top: "20" });
    const s1 = addActivity(s0, "A4", { x: 1, y: 2 });
    const s2 = connect(s1, "A3", "T9", "A5");
    delete s2.layout.A4;
    const s3 = autoLayout(s2);
    expect(s3.layout.A1).toEqual(s0.layout.A1);
    expect(s3.layout.A2).toEqual(s0.layout.A2);
    expect(s3.layout.A4).toBeDefined();
    expect(s3.layout.A5).toBeDefined();
    for (const id of ["A4", "A5"]) {
      expect(s3.layout[id].left).toMatch(/^\d+$/);
      expect(s3.layout[id].top).toMatch(/^\d+$/);
    }
    expect(autoLayout(s3)).toEqual(s3);
    // empty layout: start in column 0, its successor one 260 px column to the right
    const bare = autoLayout({ ...s0, layout: {} });
    expect(bare.layout.A1).toEqual({ left: "40", top: "40" });
    expect(bare.layout.A2.left).toBe("300");
  });

  it("setStart sets activity and dialog", () => {
    expect(setStart(shapeOf(sample()), "A2", "D9")).toMatchObject({ startActivity: "A2", startActivityDialog: "D9" });
  });

  it("shapeOf(toCanvas-inverse) round-trips the sample draft", () => {
    const draft = sample();
    const { nodes, edges } = toCanvas(draft);
    const rebuilt: ProcessShape = {
      path: Object.fromEntries(nodes.map((n) => [n.id, {}])),
      layout: Object.fromEntries(nodes.map((n) => [n.id, { left: String(n.position.x), top: String(n.position.y) }])),
      startActivity: nodes.find((n) => n.data.isStart)?.id ?? "",
      startActivityDialog: "D1",
    };
    for (const e of edges) {
      rebuilt.path[e.source][e.data.transitionId] = {
        ActivityEntityID: e.target,
        ...(e.data.actions.length ? { TransitionAction: e.data.actions.map((a) => a.id) } : {}),
      };
    }
    const original = shapeOf(draft);
    expect(isDirty(rebuilt, original)).toBe(false);
    expect(rebuilt.path.A1).toEqual(original.path.A1);
    expect(rebuilt.layout).toEqual(original.layout);
  });

  it("isDirty ignores key order", () => {
    const a = shapeOf(sample());
    const reordered: ProcessShape = {
      startActivityDialog: a.startActivityDialog, startActivity: a.startActivity,
      layout: { A3: { top: "60", left: "560" }, A2: { top: "60", left: "300" }, A1: { top: "60", left: "40" } },
      path: { A3: {}, A2: { T3: { ActivityEntityID: "A1" }, T2: { ActivityEntityID: "A3" } }, A1: { T1: { TransitionAction: ["TA1"], ActivityEntityID: "A2" } } },
    };
    expect(isDirty(a, reordered)).toBe(false);
    expect(isDirty(a, move(a, "A1", { x: 41, y: 60 }))).toBe(true);
    expect(isDirty(a, { ...a, layout: { ...a.layout, A1: { left: 40, top: 60 } } })).toBe(false);
  });
});
