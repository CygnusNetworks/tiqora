/** Shared types + pure helpers for the smart search bar and command palette. */

export type QueueOption = { id: number; name: string };
export type AgentOption = { id: number; full_name: string; login: string };

export type SmartSearchValues = {
  q: string;
  queueIds: number[];
  stateTypes: string[];
  ownerId?: number;
  customerId?: string;
  customerLabel?: string;
  createdFrom?: string;
  createdTo?: string;
};

/** Partial filter patch, keyed by the /api/v1/search param names. */
export type SmartPatch = Partial<{
  queue_id: number[];
  state_type: string[];
  owner_id?: number;
  customer_id?: string;
  customer_label?: string;
  created_from?: string;
  created_to?: string;
}>;

/** Apply a {@link SmartPatch} onto local {@link SmartSearchValues} (for consumers
 * that hold the state locally instead of in the URL, e.g. the command palette). */
export function applySmartPatch(v: SmartSearchValues, p: SmartPatch): SmartSearchValues {
  const n = { ...v };
  if ("queue_id" in p) n.queueIds = p.queue_id ?? [];
  if ("state_type" in p) n.stateTypes = p.state_type ?? [];
  if ("owner_id" in p) n.ownerId = p.owner_id ?? undefined;
  if ("customer_id" in p) n.customerId = p.customer_id ?? undefined;
  if ("customer_label" in p) n.customerLabel = p.customer_label ?? undefined;
  if ("created_from" in p) n.createdFrom = p.created_from ?? undefined;
  if ("created_to" in p) n.createdTo = p.created_to ?? undefined;
  return n;
}

/** Convert values to the /agent/search route search params (empties dropped). */
export function smartValuesToSearchParams(v: SmartSearchValues) {
  return {
    q: v.q.trim() || undefined,
    queue_id: v.queueIds.length ? v.queueIds : undefined,
    state_type: v.stateTypes.length ? v.stateTypes : undefined,
    owner_id: v.ownerId,
    customer_id: v.customerId,
    customer_label: v.customerLabel,
    created_from: v.createdFrom,
    created_to: v.createdTo,
  };
}
