import { ciRefHref } from "@/lib/ciDisplay";
import type { LinkGraph } from "@/lib/itsmApi";

export type GraphNode = LinkGraph["nodes"][number];

export const W = 200;
export const H = 46;
const GAP_X = 80;
const GAP_Y = 14;
/** `ci:znuny:45` / `znuny:45` -> `znuny:45`. */
function ciRef(key: string): string {
  return key.startsWith("ci:") ? key.slice(3) : key;
}

/** Where a click on the node goes: the ticket zoom or the CI detail page. */
export function nodeHref(n: GraphNode): string | null {
  if (n.kind === "ticket") {
    const id = /^ticket:(\d+)$/.exec(n.key)?.[1];
    return id ? `/agent/tickets/${id}` : null;
  }
  return ciRefHref(ciRef(n.key));
}

/** Pure layout: one column per depth, rows in node order. */
export function layoutGraph(nodes: GraphNode[]) {
  const rows = new Map<number, number>();
  const pos = new Map<string, { x: number; y: number }>();
  for (const n of nodes) {
    const row = rows.get(n.depth) ?? 0;
    rows.set(n.depth, row + 1);
    pos.set(n.key, { x: n.depth * (W + GAP_X), y: row * (H + GAP_Y) });
  }
  const cols = Math.max(0, ...nodes.map((n) => n.depth)) + 1;
  const maxRows = Math.max(1, ...rows.values());
  return { pos, width: cols * W + (cols - 1) * GAP_X, height: maxRows * H + (maxRows - 1) * GAP_Y };
}

