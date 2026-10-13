import type { ImpactGraph } from "@/lib/cmdbApi";

/** Column / row spacing of the layered layout (px). Fixed on purpose: no layout dependency. */
export const COLUMN_WIDTH = 340;
export const ROW_HEIGHT = 84;

type GraphNode = ImpactGraph["nodes"][number];
type GraphEdge = ImpactGraph["edges"][number];

export type LaidOutNode = { id: string; x: number; y: number };
export type LaidOutEdge = { id: string; source: string; target: string; link_type: string; propagates: boolean };
export type ImpactLayout = { nodes: LaidOutNode[]; edges: LaidOutEdge[] };

/** What the user reads for a node: `number – name` for a CI, the name for a service, id as last resort. */
export function nodeLabel(n: GraphNode): string {
  if (n.number && n.name) return `${n.number} – ${n.name}`;
  return n.name ?? n.number ?? n.id;
}

function hops(root: string, edges: GraphEdge[], forward: boolean): Map<string, number> {
  const next = new Map<string, string[]>();
  for (const e of edges) {
    const [from, to] = forward ? [e.source, e.target] : [e.target, e.source];
    next.set(from, [...(next.get(from) ?? []), to]);
  }
  const dist = new Map<string, number>([[root, 0]]);
  const queue = [root];
  for (let i = 0; i < queue.length; i++) {
    const cur = queue[i];
    for (const n of next.get(cur) ?? []) {
      if (!dist.has(n)) {
        dist.set(n, (dist.get(cur) ?? 0) + 1);
        queue.push(n);
      }
    }
  }
  return dist;
}

/**
 * Signed layer of every node: positive for what the root reaches along the link direction
 * (source to target), negative for what reaches the root. The server sends unsigned BFS depths,
 * so the sign comes from the edges; a node no directed path connects to the root keeps the sign
 * of its own `depth` (positive when that is unsigned).
 */
export function signedDepths(graph: ImpactGraph): Map<string, number> {
  const fwd = hops(graph.root, graph.edges, true);
  const back = hops(graph.root, graph.edges, false);
  const out = new Map<string, number>();
  for (const n of graph.nodes) {
    const f = fwd.get(n.id);
    const b = back.get(n.id);
    let d: number;
    if (n.id === graph.root) d = 0;
    else if (f !== undefined && (b === undefined || f <= b)) d = f;
    else if (b !== undefined) d = -b;
    else d = n.depth === 0 ? 1 : n.depth;
    out.set(n.id, d + 0); // `+ 0` turns -0 into 0
  }
  return out;
}

const KIND_ORDER: Record<string, number> = { ci: 0, service: 1 };

/** Layered layout: x by signed depth, y by position inside the layer (kind, then label, then id). */
export function layoutImpact(graph: ImpactGraph): ImpactLayout {
  const depths = signedDepths(graph);
  const layers = new Map<number, GraphNode[]>();
  for (const n of graph.nodes) {
    const d = depths.get(n.id) ?? 0;
    layers.set(d, [...(layers.get(d) ?? []), n]);
  }
  const nodes: LaidOutNode[] = [];
  for (const [d, members] of layers) {
    const sorted = [...members].sort(
      (a, b) =>
        (KIND_ORDER[a.kind] ?? 9) - (KIND_ORDER[b.kind] ?? 9) ||
        Number(!!a.hidden) - Number(!!b.hidden) ||
        nodeLabel(a).localeCompare(nodeLabel(b), "de") ||
        a.id.localeCompare(b.id),
    );
    sorted.forEach((n, i) => nodes.push({ id: n.id, x: d * COLUMN_WIDTH + 0, y: (i - (sorted.length - 1) / 2) * ROW_HEIGHT + 0 }));
  }
  nodes.sort((a, b) => a.x - b.x || a.y - b.y || a.id.localeCompare(b.id));
  const known = new Set(graph.nodes.map((n) => n.id));
  const edges = graph.edges
    .filter((e) => known.has(e.source) && known.has(e.target))
    .map((e) => ({ id: `${e.source}>${e.target}:${e.link_type}`, source: e.source, target: e.target, link_type: e.link_type, propagates: e.propagates }));
  return { nodes, edges };
}

export type ImpactTreeItem = {
  node: GraphNode;
  /** The edge that brought this node into the tree (absent for the root). */
  via?: { link_type: string; propagates: boolean; outgoing: boolean };
  children: ImpactTreeItem[];
};

/**
 * Spanning tree of the graph from the root for the text list (small screens, screen readers):
 * every node appears once, under the neighbour that reached it first, children sorted like the canvas.
 */
export function impactTree(graph: ImpactGraph): ImpactTreeItem | null {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const root = byId.get(graph.root);
  if (!root) return null;
  const rootItem: ImpactTreeItem = { node: root, children: [] };
  const items = new Map<string, ImpactTreeItem>([[root.id, rootItem]]);
  const queue = [root.id];
  for (let i = 0; i < queue.length; i++) {
    const cur = items.get(queue[i]) as ImpactTreeItem;
    const reached: ImpactTreeItem[] = [];
    for (const e of graph.edges) {
      const outgoing = e.source === cur.node.id;
      const otherId = outgoing ? e.target : e.source;
      if (!outgoing && e.target !== cur.node.id) continue;
      const other = byId.get(otherId);
      if (!other || items.has(otherId)) continue;
      const item: ImpactTreeItem = { node: other, via: { link_type: e.link_type, propagates: e.propagates, outgoing }, children: [] };
      items.set(otherId, item);
      reached.push(item);
      queue.push(otherId);
    }
    cur.children = reached.sort(
      (a, b) =>
        (KIND_ORDER[a.node.kind] ?? 9) - (KIND_ORDER[b.node.kind] ?? 9) ||
        Number(!!a.node.hidden) - Number(!!b.node.hidden) ||
        nodeLabel(a.node).localeCompare(nodeLabel(b.node), "de"),
    );
  }
  return rootItem;
}
