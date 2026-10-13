import { describe, expect, it } from "vitest";
import type { AttributeDef } from "@/lib/cmdbApi";
import {
  addAttr,
  fromDefinition,
  localIssues,
  moveAttr,
  removeAttr,
  toDefinition,
  uidForPath,
  updateAttr,
} from "./definitionModel";

const attr = (a: Partial<AttributeDef> & Pick<AttributeDef, "key" | "name" | "type">): AttributeDef => ({
  count_min: 0,
  count_max: 1,
  count_default: 1,
  required: false,
  searchable: false,
  portal: false,
  options: { multiline: false },
  sub: [],
  ...a,
});

const subKeys = (tree: Parameters<typeof toDefinition>[0], i: number) => (toDefinition(tree)[i].sub ?? []).map((s) => s.key);

const NIC = attr({
  key: "nic",
  name: "Netzwerkkarte",
  type: "group",
  count_max: 4,
  sub: [attr({ key: "ip", name: "IP", type: "ip", count_max: 3 }), attr({ key: "mac", name: "MAC", type: "mac" })],
});
const DEFS: AttributeDef[] = [attr({ key: "hostname", name: "Hostname", type: "text", required: true, searchable: true }), NIC];

describe("definitionModel", () => {
  it("round-trips a definition through the draft tree unchanged", () => {
    expect(toDefinition(fromDefinition(DEFS))).toEqual(DEFS);
  });

  it("gives every node of the tree a distinct uid", () => {
    const tree = fromDefinition(DEFS);
    const uids = [tree[0].uid, tree[1].uid, tree[1].sub[0].uid, tree[1].sub[1].uid];
    expect(new Set(uids).size).toBe(4);
  });

  it("adds, nests, moves and removes attributes and the result matches the expected definition", () => {
    let tree = fromDefinition([DEFS[0]]);
    const g = addAttr(tree, null, "group");
    tree = updateAttr(g.tree, g.uid, { key: "nic", name: "Netzwerkkarte", count_max: 4 });
    const child = addAttr(tree, g.uid, "ip");
    tree = updateAttr(child.tree, child.uid, { key: "ip", name: "IP", count_max: 3 });
    const mac = addAttr(tree, g.uid, "mac");
    tree = updateAttr(mac.tree, mac.uid, { key: "mac", name: "MAC" });
    expect(toDefinition(tree)).toEqual(DEFS);

    tree = moveAttr(tree, mac.uid, "up");
    expect(subKeys(tree, 1)).toEqual(["mac", "ip"]);
    tree = moveAttr(tree, mac.uid, "up"); // already first: no-op
    expect(subKeys(tree, 1)).toEqual(["mac", "ip"]);
    tree = moveAttr(tree, g.uid, "up");
    expect(toDefinition(tree).map((a) => a.key)).toEqual(["nic", "hostname"]);

    tree = removeAttr(tree, mac.uid);
    expect(subKeys(tree, 0)).toEqual(["ip"]);
  });

  it("does not mutate the input tree", () => {
    const tree = fromDefinition(DEFS);
    const snapshot = JSON.stringify(tree);
    updateAttr(tree, tree[0].uid, { name: "Anders" });
    removeAttr(tree, tree[1].uid);
    moveAttr(tree, tree[1].uid, "up");
    expect(JSON.stringify(tree)).toBe(snapshot);
  });

  it("proposes a unique key among the siblings for a new attribute", () => {
    const tree = fromDefinition(DEFS);
    const a = addAttr(tree, null, "text");
    const b = addAttr(a.tree, null, "text");
    const keys = toDefinition(b.tree).map((x) => x.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("clears sub attributes, searchable and default when the type changes to attachment", () => {
    const tree = fromDefinition(DEFS);
    const out = toDefinition(updateAttr(tree, tree[1].uid, { type: "attachment" }));
    expect(out[1].type).toBe("attachment");
    expect(out[1].sub).toEqual([]);
    const fresh = fromDefinition(DEFS);
    const t2 = updateAttr(fresh, fresh[0].uid, { type: "attachment" });
    expect(t2[0].searchable).toBe(false);
  });

  it("flags duplicate sibling keys (case-insensitive) on both nodes, but not equal keys in different groups", () => {
    const tree = fromDefinition([
      attr({ key: "ip", name: "A", type: "ip" }),
      attr({ key: "IP", name: "B", type: "ip" }),
      attr({ key: "grp", name: "G", type: "group", sub: [attr({ key: "ip", name: "C", type: "ip" })] }),
    ]);
    const issues = localIssues(tree);
    expect(issues[tree[0].uid]).toBe("duplicate_key");
    expect(issues[tree[1].uid]).toBe("duplicate_key");
    expect(issues[tree[2].sub[0].uid]).toBeUndefined();
  });

  it("flags bad keys, empty names, count ranges and groups without sub attributes", () => {
    const tree = fromDefinition([
      attr({ key: "1bad", name: "A", type: "text" }),
      attr({ key: "ok", name: " ", type: "text" }),
      attr({ key: "cnt", name: "C", type: "text", count_min: 3, count_max: 2, count_default: 2 }),
      attr({ key: "def", name: "D", type: "text", count_min: 0, count_max: 2, count_default: 5 }),
      attr({ key: "grp", name: "G", type: "group" }),
    ]);
    const issues = localIssues(tree);
    expect(issues[tree[0].uid]).toBe("bad_key");
    expect(issues[tree[1].uid]).toBe("name_empty");
    expect(issues[tree[2].uid]).toBe("count_range");
    expect(issues[tree[3].uid]).toBe("count_range");
    expect(issues[tree[4].uid]).toBe("group_without_sub");
  });

  it("reports no issues for a valid definition", () => {
    expect(localIssues(fromDefinition(DEFS))).toEqual({});
  });

  it("maps a server issue path to the uid of the attribute", () => {
    const tree = fromDefinition(DEFS);
    expect(uidForPath(tree, "nic.mac")).toBe(tree[1].sub[1].uid);
    expect(uidForPath(tree, "hostname")).toBe(tree[0].uid);
    expect(uidForPath(tree, "nic.nope")).toBeNull();
  });
});
