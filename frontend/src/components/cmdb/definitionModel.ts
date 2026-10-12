/**
 * Pure model of the class designer (D3 / D10): the attribute tree being edited, the editing helpers and
 * a quick client-side check. The server's `/definitions/validate` stays authoritative; the local check
 * only catches what is obvious without a round trip (same key pattern and count limits as
 * `backend/src/tiqora/cmdb/definition.py`).
 */
import type { AttributeDef } from "@/lib/cmdbApi";

export type AttrType = AttributeDef["type"];
export type DraftAttr = Omit<AttributeDef, "sub"> & { uid: string; sub: DraftAttr[] };
export type MoveDir = "up" | "down";
/** `Record<uid, code>`: one code per attribute, shown as `admin.cmdb.editor.issue.<code>`. */
export type LocalIssues = Record<string, LocalIssueCode>;
export type LocalIssueCode = "bad_key" | "duplicate_key" | "name_empty" | "count_range" | "group_without_sub";

export const ATTR_KEY_PATTERN = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
export const MAX_COUNT = 100;

/** Every attribute type in the order of the type picker. */
export const ATTR_TYPES: readonly AttrType[] = [
  "text",
  "textarea",
  "integer",
  "decimal",
  "money",
  "date",
  "datetime",
  "url",
  "email",
  "ip",
  "mac",
  "catalog",
  "ci_ref",
  "customer_user",
  "customer_company",
  "agent",
  "service",
  "sla",
  "queue",
  "priority",
  "ticket_type",
  "state",
  "attachment",
  "group",
];

/** Types without a default value (and, for both, not searchable). */
const NO_DEFAULT: ReadonlySet<AttrType> = new Set(["group", "attachment"]);
export const hasNoDefault = (type: AttrType): boolean => NO_DEFAULT.has(type);
export const isNotSearchable = (type: AttrType): boolean => NO_DEFAULT.has(type);

let uidCounter = 0;
const nextUid = (): string => `a${++uidCounter}`;

export function fromDefinition(defs: readonly AttributeDef[]): DraftAttr[] {
  return defs.map((d) => ({
    ...d,
    options: { multiline: false, ...d.options, ...(d.options?.class_keys ? { class_keys: [...d.options.class_keys] } : {}) },
    uid: nextUid(),
    sub: fromDefinition(d.sub ?? []),
  }));
}

export function toDefinition(tree: readonly DraftAttr[]): AttributeDef[] {
  return tree.map((n) => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { uid, sub, ...rest } = n;
    return { ...rest, sub: toDefinition(sub) } as AttributeDef;
  });
}

function blank(type: AttrType, key: string): DraftAttr {
  const node: DraftAttr = {
    uid: nextUid(),
    key,
    name: "",
    type,
    required: false,
    searchable: false,
    portal: false,
    count_min: 0,
    count_max: 1,
    count_default: 1,
    options: { multiline: false },
    sub: [],
  };
  if (type === "money") node.options = { multiline: false, currency: "EUR" };
  return node;
}

function uniqueKey(siblings: readonly DraftAttr[], type: AttrType): string {
  const taken = new Set(siblings.map((s) => s.key.toLowerCase()));
  for (let n = 1; ; n++) {
    const key = `${type}${n}`;
    if (!taken.has(key.toLowerCase())) return key;
  }
}

function mapList(list: readonly DraftAttr[], fn: (siblings: DraftAttr[]) => DraftAttr[] | null, parentUid: string | null): DraftAttr[] {
  return list.map((n) => ({ ...n, sub: parentUid === n.uid ? (fn(n.sub) ?? n.sub) : mapList(n.sub, fn, parentUid) }));
}

/** Adds a new attribute at the end of the top level (`parentUid` null) or of a group. */
export function addAttr(tree: readonly DraftAttr[], parentUid: string | null, type: AttrType): { tree: DraftAttr[]; uid: string } {
  const probe = parentUid === null ? tree : (findNode(tree, parentUid)?.sub ?? []);
  const node = blank(type, uniqueKey(probe, type));
  if (parentUid === null) return { tree: [...tree, node], uid: node.uid };
  return { tree: mapList(tree, (sibs) => [...sibs, node], parentUid), uid: node.uid };
}

export function findNode(tree: readonly DraftAttr[], uid: string): DraftAttr | null {
  for (const n of tree) {
    if (n.uid === uid) return n;
    const hit = findNode(n.sub, uid);
    if (hit) return hit;
  }
  return null;
}

function editSiblings(tree: readonly DraftAttr[], uid: string, fn: (siblings: DraftAttr[], index: number) => DraftAttr[]): DraftAttr[] {
  const index = tree.findIndex((n) => n.uid === uid);
  if (index >= 0) return fn([...tree], index);
  return tree.map((n) => ({ ...n, sub: editSiblings(n.sub, uid, fn) }));
}

export function moveAttr(tree: readonly DraftAttr[], uid: string, dir: MoveDir): DraftAttr[] {
  return editSiblings(tree, uid, (sibs, i) => {
    const j = dir === "up" ? i - 1 : i + 1;
    if (j < 0 || j >= sibs.length) return sibs;
    [sibs[i], sibs[j]] = [sibs[j], sibs[i]];
    return sibs;
  });
}

export function removeAttr(tree: readonly DraftAttr[], uid: string): DraftAttr[] {
  return editSiblings(tree, uid, (sibs, i) => {
    sibs.splice(i, 1);
    return sibs;
  });
}

export type AttrPatch = Partial<Omit<DraftAttr, "uid" | "sub">>;

/** Patches one attribute; a type change drops what the new type cannot carry (sub attributes, default, searchable). */
export function updateAttr(tree: readonly DraftAttr[], uid: string, patch: AttrPatch): DraftAttr[] {
  return tree.map((n) => {
    if (n.uid !== uid) return { ...n, sub: updateAttr(n.sub, uid, patch) };
    const next: DraftAttr = { ...n, ...patch };
    if (patch.type !== undefined && patch.type !== n.type) {
      if (patch.type !== "group") next.sub = [];
      if (hasNoDefault(patch.type)) next.default = null;
      if (isNotSearchable(patch.type)) next.searchable = false;
      if (patch.type === "money" && !next.options?.currency) next.options = { multiline: false, ...next.options, currency: "EUR" };
    }
    return next;
  });
}

/** Server issue path (`nic.mac`, keys joined by dots) to the uid of the attribute, or null. */
export function uidForPath(tree: readonly DraftAttr[], path: string): string | null {
  const walk = (list: readonly DraftAttr[], keys: string[]): string | null => {
    const hit = list.find((n) => n.key === keys[0]);
    if (!hit) return null;
    return keys.length === 1 ? hit.uid : walk(hit.sub, keys.slice(1));
  };
  return path ? walk(tree, path.split(".")) : null;
}

function countOk(n: DraftAttr): boolean {
  const { count_min: lo, count_max: hi, count_default: d } = n;
  return [lo, hi, d].every(Number.isInteger) && lo >= 0 && hi >= 1 && hi <= MAX_COUNT && lo <= hi && d >= lo && d <= hi;
}

/** Obvious problems per attribute (first match wins); empty when the tree is fine. */
export function localIssues(tree: readonly DraftAttr[]): LocalIssues {
  const out: LocalIssues = {};
  const walk = (list: readonly DraftAttr[]) => {
    const seen = new Map<string, number>();
    for (const n of list) seen.set(n.key.toLowerCase(), (seen.get(n.key.toLowerCase()) ?? 0) + 1);
    for (const n of list) {
      if (!ATTR_KEY_PATTERN.test(n.key)) out[n.uid] = "bad_key";
      else if ((seen.get(n.key.toLowerCase()) ?? 0) > 1) out[n.uid] = "duplicate_key";
      else if (n.name.trim() === "") out[n.uid] = "name_empty";
      else if (!countOk(n)) out[n.uid] = "count_range";
      else if (n.type === "group" && n.sub.length === 0) out[n.uid] = "group_without_sub";
      walk(n.sub);
    }
  };
  walk(tree);
  return out;
}
