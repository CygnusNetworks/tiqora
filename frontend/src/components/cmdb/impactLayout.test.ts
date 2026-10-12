import { describe, expect, it } from "vitest";
import type { ImpactGraph } from "@/lib/cmdbApi";
import { COLUMN_WIDTH, impactTree, layoutImpact, nodeLabel, signedDepths } from "./impactLayout";

const g = {
  root: "ci:1",
  truncated: false,
  nodes: [{ id: "ci:1", depth: 0 }, { id: "ci:2", depth: 1 }, { id: "ci:3", depth: -1 }, { id: "service:9", depth: -1, kind: "service" }],
  edges: [{ source: "ci:3", target: "ci:1" }, { source: "ci:1", target: "ci:2" }, { source: "service:9", target: "ci:1" }],
} as unknown as ImpactGraph;

describe("layoutImpact", () => {
  it("puts upstream left, downstream right, root at x=0, deterministic", () => {
    const a = layoutImpact(g);
    const b = layoutImpact(g);
    expect(a).toEqual(b);
    const x = Object.fromEntries(a.nodes.map((n) => [n.id, n.x]));
    expect(x["ci:3"]).toBeLessThan(0);
    expect(x["ci:1"]).toBe(0);
    expect(x["ci:2"]).toBeGreaterThan(0);
    expect(x["ci:2"]).toBe(COLUMN_WIDTH);
  });

  it("derives the sign from the edges when the server sends unsigned depths", () => {
    const unsigned = {
      root: "ci:1",
      truncated: false,
      nodes: [
        { id: "ci:1", kind: "ci", hidden: false, depth: 0 },
        { id: "ci:2", kind: "ci", hidden: false, depth: 1 },
        { id: "ci:3", kind: "ci", hidden: false, depth: 1 },
        { id: "ci:4", kind: "ci", hidden: false, depth: 2 },
      ],
      edges: [
        { source: "ci:1", target: "ci:2", link_type: "DependsOn", propagates: true },
        { source: "ci:3", target: "ci:1", link_type: "DependsOn", propagates: true },
        { source: "ci:4", target: "ci:3", link_type: "DependsOn", propagates: false },
      ],
    } as unknown as ImpactGraph;
    const d = signedDepths(unsigned);
    expect([d.get("ci:1"), d.get("ci:2"), d.get("ci:3"), d.get("ci:4")]).toEqual([0, 1, -1, -2]);
  });

  it("orders a layer by kind, then label, and centres it vertically", () => {
    const graph = {
      root: "ci:1",
      truncated: false,
      nodes: [
        { id: "ci:1", kind: "ci", hidden: false, depth: 0, number: "SRV-1", name: "root" },
        { id: "service:7", kind: "service", hidden: false, depth: 1, name: "Aaa" },
        { id: "ci:3", kind: "ci", hidden: false, depth: 1, number: "SRV-3", name: "zeta" },
        { id: "ci:2", kind: "ci", hidden: false, depth: 1, number: "SRV-2", name: "beta" },
      ],
      edges: [
        { source: "ci:1", target: "service:7", link_type: "DependsOn", propagates: false },
        { source: "ci:1", target: "ci:3", link_type: "DependsOn", propagates: false },
        { source: "ci:1", target: "ci:2", link_type: "DependsOn", propagates: false },
      ],
    } as unknown as ImpactGraph;
    const col = layoutImpact(graph).nodes.filter((n) => n.x === COLUMN_WIDTH);
    const byY = [...col].sort((a, b) => a.y - b.y).map((n) => n.id);
    expect(byY).toEqual(["ci:2", "ci:3", "service:7"]);
    expect(col.reduce((s, n) => s + n.y, 0)).toBe(0);
  });

  it("drops edges whose end is not in the node list", () => {
    const graph = { ...g, edges: [...g.edges, { source: "ci:1", target: "ci:99", link_type: "X", propagates: false }] } as unknown as ImpactGraph;
    expect(layoutImpact(graph).edges).toHaveLength(3);
  });
});

describe("impactTree", () => {
  it("lists every node once under the neighbour that reached it", () => {
    const tree = impactTree(g);
    expect(tree?.node.id).toBe("ci:1");
    const ids = (tree?.children ?? []).map((c) => c.node.id).sort();
    expect(ids).toEqual(["ci:2", "ci:3", "service:9"]);
    expect(tree?.children.find((c) => c.node.id === "ci:2")?.via?.outgoing).toBe(true);
    expect(tree?.children.find((c) => c.node.id === "ci:3")?.via?.outgoing).toBe(false);
  });
});

describe("nodeLabel", () => {
  it("prefers number and name, falls back to the id", () => {
    expect(nodeLabel({ id: "ci:5", number: "SRV-5", name: "web" } as unknown as ImpactGraph["nodes"][number])).toBe("SRV-5 – web");
    expect(nodeLabel({ id: "ci:5" } as unknown as ImpactGraph["nodes"][number])).toBe("ci:5");
  });
});
