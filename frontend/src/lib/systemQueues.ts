import type { QueueNode } from "@/lib/api";

/** Znuny's stock plumbing queues: Postmaster (fallback for unroutable mail),
 * Raw (PostmasterDefaultQueue) and Junk (spam). Znuny has no "system" flag,
 * so they are recognised by their stock names — including their sub-queues. */
const SYSTEM_QUEUE_NAMES = new Set(["postmaster", "raw", "junk"]);

export function isSystemQueue(node: Pick<QueueNode, "name">): boolean {
  const root = node.name.split("::")[0]?.trim().toLowerCase() ?? "";
  return SYSTEM_QUEUE_NAMES.has(root);
}
