/**
 * Edit model of the shared filter builder: converts between the wire filter
 * (`Schemas["Filter"]`, `backend/src/tiqora/filters/model.py`) and a small tree
 * of conditions the UI can edit (one root group plus one level of groups).
 * Pure, no React.
 */
import type { TicketFilter } from "@/lib/savedSearchesApi";

export type FilterFieldType = "id" | "string" | "int" | "datetime" | "bool" | "enum";

export type FilterFieldDef = {
  key: string;
  label: string;
  type: FilterFieldType;
  /** Operators the field allows (as the API sends them). */
  ops: string[];
  enumValues: { value: string; label: string }[];
  /** "queue" | "state" | "priority" | "type" | "service" | "sla" | "user" | "df:<Name>" | null */
  optionsSource: string | null;
};

/** Copy of the backend `OPS_BY_TYPE`; `opsByType.fixture.json` pins the equality. */
export const OPS_BY_TYPE: Record<FilterFieldType, readonly string[]> = {
  id: ["eq", "neq", "in", "not_in"],
  string: ["eq", "neq", "in", "not_in", "contains", "starts_with", "is_empty", "is_not_empty"],
  int: ["eq", "neq", "lt", "lte", "gt", "gte", "between", "in"],
  datetime: ["before", "after", "between", "within_last", "older_than", "is_empty", "is_not_empty"],
  bool: ["is_true", "is_false"],
  enum: ["eq", "neq", "in", "not_in"],
};

export type EditCondition = { kind: "condition"; id: string; field: string; op: string; value: unknown };
export type EditGroup = {
  kind: "group";
  id: string;
  mode: "all" | "any";
  negate: boolean;
  children: (EditCondition | EditGroup)[];
};

/** Root group + one nested group. */
export const MAX_UI_DEPTH = 2;

export const NO_VALUE_OPS: readonly string[] = ["is_empty", "is_not_empty", "is_true", "is_false"];
export const LIST_OPS: readonly string[] = ["in", "not_in"];
export const RELATIVE_OPS: readonly string[] = ["within_last", "older_than"];
export const RELATIVE_UNITS = ["minute", "hour", "day", "week"] as const;
/** Operator a new condition starts with (first one the field allows). */
const DEFAULT_OP_ORDER = ["eq", "in", "is_true", "before", "contains"];

let counter = 0;
export function newId(): string {
  counter += 1;
  return `f${counter}`;
}

type Node = NonNullable<TicketFilter["where"]>;
type Wire = Record<string, unknown>;

export function emptyGroup(mode: "all" | "any" = "all"): EditGroup {
  return { kind: "group", id: newId(), mode, negate: false, children: [] };
}

/** First of the preferred operators the field allows, else its first operator. */
export function defaultOp(ops: readonly string[]): string {
  return DEFAULT_OP_ORDER.find((op) => ops.includes(op)) ?? ops[0] ?? "eq";
}

export function emptyValueFor(op: string, type: FilterFieldType): unknown {
  if (NO_VALUE_OPS.includes(op)) return undefined;
  if (LIST_OPS.includes(op)) return [];
  if (op === "between") return { from: "", to: "" };
  if (RELATIVE_OPS.includes(op)) return { amount: 1, unit: "day" };
  void type;
  return "";
}

/** Coarse value shape of an operator; the value survives an operator change within one shape. */
export function valueShape(op: string): "none" | "list" | "range" | "relative" | "scalar" {
  if (NO_VALUE_OPS.includes(op)) return "none";
  if (LIST_OPS.includes(op)) return "list";
  if (op === "between") return "range";
  if (RELATIVE_OPS.includes(op)) return "relative";
  return "scalar";
}

function isObject(v: unknown): v is Wire {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function condition(raw: Wire): EditCondition {
  return {
    kind: "condition",
    id: newId(),
    field: String(raw.field),
    op: String(raw.op),
    value: raw.value === null ? undefined : raw.value,
  };
}

/** Group body (`all` / `any`) at `depth`; null when the UI cannot show it. */
function group(raw: Wire, depth: number, negate: boolean): EditGroup | null {
  const mode = Array.isArray(raw.all) ? "all" : Array.isArray(raw.any) ? "any" : null;
  if (mode === null || depth > MAX_UI_DEPTH) return null;
  const children: EditGroup["children"] = [];
  for (const child of raw[mode] as unknown[]) {
    if (!isObject(child)) return null;
    if ("field" in child) {
      children.push(condition(child));
      continue;
    }
    let inner: Wire = child;
    let childNegate = false;
    if (isObject(child.not) && ("all" in child.not || "any" in child.not)) {
      inner = child.not;
      childNegate = true;
    }
    const nested = group(inner, depth + 1, childNegate);
    if (nested === null) return null;
    children.push(nested);
  }
  return { kind: "group", id: newId(), mode, negate, children };
}

export function fromFilter(filter: TicketFilter | null | undefined): { root: EditGroup; editable: boolean } {
  const where = filter?.where as Wire | null | undefined;
  if (!where) return { root: emptyGroup(), editable: true };
  if ("field" in where) {
    // a lone condition: show it as an "all" group of one
    return { root: { ...emptyGroup(), children: [condition(where)] }, editable: true };
  }
  let body: Wire = where;
  let negate = false;
  if (isObject(where.not) && ("all" in where.not || "any" in where.not)) {
    body = where.not;
    negate = true;
  }
  const root = group(body, 1, negate);
  return root ? { root, editable: true } : { root: emptyGroup(), editable: false };
}

function nodeToWire(node: EditCondition | EditGroup): Node | null {
  if (node.kind === "condition") {
    const out: Wire = { field: node.field, op: node.op };
    if (node.value !== undefined) out.value = node.value;
    return out as Node;
  }
  const children = node.children.map(nodeToWire).filter((c): c is Node => c !== null);
  if (children.length === 0) return null;
  const body = { [node.mode]: children } as Node;
  return (node.negate ? { not: body } : body) as Node;
}

/** Empty root -> `{version: 1}`; a root with one child stays a group (stable round trip). */
export function toFilter(root: EditGroup): TicketFilter {
  const where = nodeToWire(root);
  return (where ? { version: 1, where } : { version: 1 }) as TicketFilter;
}

const INT = /^-?\d+$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

function isInt(v: unknown): boolean {
  return (typeof v === "number" && Number.isInteger(v)) || (typeof v === "string" && INT.test(v.trim()));
}

function itemError(v: unknown, field: FilterFieldDef): string | null {
  switch (field.type) {
    case "id":
    case "int":
      return isInt(v) ? null : "filters.invalid.number";
    case "datetime":
      return typeof v === "string" && DATE.test(v) && !Number.isNaN(Date.parse(v))
        ? null
        : "filters.invalid.date";
    case "enum":
      return typeof v === "string" && field.enumValues.some((e) => e.value === v)
        ? null
        : "filters.invalid.required";
    default:
      return typeof v === "string" && v.trim() !== "" ? null : "filters.invalid.required";
  }
}

/** i18n key of what is wrong with a condition, or null. */
export function conditionError(c: EditCondition, field: FilterFieldDef | undefined): string | null {
  if (!field || !field.ops.includes(c.op)) return "filters.invalid.required";
  switch (valueShape(c.op)) {
    case "none":
      return null;
    case "list": {
      if (!Array.isArray(c.value) || c.value.length === 0) return "filters.invalid.list";
      for (const item of c.value) {
        const err = itemError(item, field);
        if (err) return err;
      }
      return null;
    }
    case "range": {
      const v = c.value as { from?: unknown; to?: unknown } | undefined;
      return itemError(v?.from, field) ?? itemError(v?.to, field);
    }
    case "relative": {
      const v = c.value as { amount?: unknown; unit?: unknown } | undefined;
      const amount = Number(v?.amount);
      if (!Number.isInteger(amount) || amount < 1) return "filters.invalid.number";
      return (RELATIVE_UNITS as readonly unknown[]).includes(v?.unit) ? null : "filters.invalid.required";
    }
    default:
      return itemError(c.value, field);
  }
}

/** Condition id -> i18n key, for every invalid condition in the tree. */
export function filterErrors(root: EditGroup, fields: FilterFieldDef[]): Map<string, string> {
  const byKey = new Map(fields.map((f) => [f.key, f]));
  const out = new Map<string, string>();
  const walk = (node: EditCondition | EditGroup) => {
    if (node.kind === "group") {
      node.children.forEach(walk);
      return;
    }
    const err = conditionError(node, byKey.get(node.field));
    if (err) out.set(node.id, err);
  };
  walk(root);
  return out;
}
