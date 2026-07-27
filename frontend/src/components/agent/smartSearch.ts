/** Shared types + pure helpers for the smart search bar and command palette. */

export type QueueOption = { id: number; name: string };
export type AgentOption = { id: number; full_name: string; login: string };

/** Display label for a customer chip / selection: name with customer number. */
export function formatCustomerLabel(name: string, customerId: string): string {
  const n = name.trim();
  const id = customerId.trim();
  if (!n) return id;
  if (!id || n === id || n.includes(id)) return n;
  return `${n} · ${id}`;
}

export type FilterKey = "queue" | "owner" | "status" | "customer" | "from" | "to";

/** Alias → canonical filter key. German + English spellings. */
export const KEY_ALIASES: Record<string, FilterKey> = {
  queue: "queue",
  besitzer: "owner",
  owner: "owner",
  status: "status",
  state: "status",
  kunde: "customer",
  customer: "customer",
  von: "from",
  from: "from",
  bis: "to",
  to: "to",
};

/** Keys offered as typeahead prefixes in the UI (German primary). */
export const FILTER_KEY_HINTS = [
  "queue",
  "besitzer",
  "status",
  "kunde",
  "von",
  "bis",
] as const;

/** Parse ``key:fragment``; returns null when the text is not a recognised key. */
export function parseKeyed(text: string): { key: FilterKey; frag: string } | null {
  const m = text.trim().match(/^([\p{L}]+):(.*)$/u);
  if (!m) return null;
  const canonical = KEY_ALIASES[m[1].toLowerCase()];
  if (!canonical) return null;
  return { key: canonical, frag: m[2] };
}

/**
 * True while the user is composing a structured filter (partial key like
 * ``que`` / ``queue`` or a full ``queue:…`` token). Free-text live query must
 * not absorb these — otherwise committing a chip leaves residual ``queue`` in
 * the input (from the pre-colon keystrokes).
 */
export function isFilterComposition(text: string): boolean {
  const raw = text.trim();
  if (!raw) return false;
  if (parseKeyed(raw)) return true;
  const low = raw.toLowerCase();
  for (const alias of Object.keys(KEY_ALIASES)) {
    if (alias.startsWith(low) || low.startsWith(alias)) return true;
  }
  for (const hint of FILTER_KEY_HINTS) {
    if (hint.startsWith(low) || low.startsWith(hint)) return true;
  }
  return false;
}

/** Last path segment for Znuny-style ``Parent::Child`` queue names. */
export function queueLeafName(name: string): string {
  const parts = name.split("::");
  return (parts[parts.length - 1] ?? name).trim();
}

/** Ranked queue matches for a fragment (starts-with first, then includes). */
export function matchQueues(
  queues: QueueOption[],
  frag: string,
  excludeIds: number[] = [],
  limit = 8,
): QueueOption[] {
  const f = frag.toLowerCase().trim();
  const available = queues.filter((q) => !excludeIds.includes(q.id));
  if (!f) return available.slice(0, limit);

  const scored: { q: QueueOption; score: number }[] = [];
  for (const q of available) {
    const name = q.name.toLowerCase();
    const leaf = queueLeafName(q.name).toLowerCase();
    let score = -1;
    if (leaf === f || name === f) score = 0;
    else if (leaf.startsWith(f) || name.startsWith(f)) score = 1;
    else if (leaf.includes(f) || name.includes(f)) score = 2;
    if (score >= 0) scored.push({ q, score });
  }
  scored.sort((a, b) => a.score - b.score || a.q.name.localeCompare(b.q.name));
  return scored.slice(0, limit).map((s) => s.q);
}

/**
 * Resolve auto-commit candidate after Space/Enter on a queue fragment.
 * Exact leaf/name match wins; otherwise a single remaining match.
 */
export function uniqueQueueMatch(
  queues: QueueOption[],
  frag: string,
  excludeIds: number[] = [],
): QueueOption | null {
  const matches = matchQueues(queues, frag, excludeIds, 20);
  if (matches.length === 0) return null;
  const f = frag.toLowerCase().trim();
  const exact = matches.find(
    (q) => q.name.toLowerCase() === f || queueLeafName(q.name).toLowerCase() === f,
  );
  if (exact) return exact;
  if (matches.length === 1) return matches[0] ?? null;
  return null;
}

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
