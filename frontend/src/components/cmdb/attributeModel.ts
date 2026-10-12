/**
 * Pure model of a CI's attribute values (D4 / D12): the `{v, s}` entry tree, the editing helpers and a
 * client-side validation that mirrors `backend/src/tiqora/cmdb/values.py` (same codes, same order of
 * the empty-entry rules, F18), so the form flags the same fields the API would.
 */
import type { TFunction } from "i18next";
import type { FieldDef, FieldKind, FieldOption, FieldValue } from "@/components/fields/types";
import type { AttributeDef, FormOptions } from "@/lib/cmdbApi";
import { fromZonedInputValue, toZonedInputValue } from "@/lib/timeZone";

export type AttrScalar = string | number | null;
export type AttrEntry = { v: AttrScalar; s?: Record<string, AttrEntry[]> };
export type AttrValues = Record<string, AttrEntry[]>;
/**
 * Position of one entry: the chain of (attribute key, entry index) from the top level down. For
 * `addEntry` the last segment names the list to add to (its index is ignored); `removeEntry` and
 * `setValue` address the entry at the last segment's index. Indices are 0-based.
 */
export type EntryPath = Array<{ key: string; index: number }>;

/** Backend value-issue codes with a `cmdb.validation.<code>` message each. */
export const VALIDATION_CODES = [
  "required",
  "too_few",
  "too_many",
  "bad_type",
  "too_long",
  "out_of_range",
  "bad_format",
  "unknown_key",
  "unknown_reference",
  "wrong_class",
] as const;
export type ValidationCode = (typeof VALIDATION_CODES)[number];

const ID_TYPES: ReadonlySet<string> = new Set([
  "catalog",
  "ci_ref",
  "agent",
  "service",
  "sla",
  "queue",
  "priority",
  "ticket_type",
  "state",
  "attachment",
]);
const SELECT_TYPES: ReadonlySet<string> = new Set(["agent", "service", "sla", "queue", "priority", "ticket_type", "state"]);
const DEFAULT_MAX_LENGTH: Record<string, number> = {
  text: 255,
  textarea: 20000,
  url: 2048,
  email: 254,
  ip: 64,
  mac: 32,
  customer_user: 250,
  customer_company: 150,
};

// ---------------------------------------------------------------- shape helpers

/** `NIC[1].IPAddress[2]` (1-based) for an {@link EntryPath}; the key syntax of the API's issue paths. */
export function pathString(path: EntryPath): string {
  return path.map((p) => `${p.key}[${p.index + 1}]`).join(".");
}

function newEntry(def: AttributeDef): AttrEntry {
  const v = def.default === undefined || def.default === null ? null : def.default;
  const subs = def.sub ?? [];
  return subs.length > 0 ? { v, s: emptyValues(subs) } : { v };
}

/** Entry count of a fresh attribute: `count_default`, kept inside `count_min..count_max`. */
function initialCount(def: AttributeDef): number {
  const max = Math.max(def.count_max ?? 1, 1);
  return Math.min(Math.max(def.count_default ?? 1, def.count_min ?? 0), max);
}

/** Fresh values: every attribute with `count_default` empty entries, sub attributes recursively. */
export function emptyValues(defs: readonly AttributeDef[]): AttrValues {
  const out: AttrValues = {};
  for (const d of defs) {
    const n = initialCount(d);
    out[d.key] = Array.from({ length: n }, () => newEntry(d));
  }
  return out;
}

function updateAt(values: AttrValues, path: EntryPath, depth: number, fn: (list: AttrEntry[]) => AttrEntry[]): AttrValues {
  const seg = path[depth];
  if (!seg) return values;
  const list = values[seg.key] ?? [];
  if (depth === path.length - 1) return { ...values, [seg.key]: fn(list) };
  const entry = list[seg.index];
  if (!entry) return values;
  const next: AttrEntry = { ...entry, s: updateAt(entry.s ?? {}, path, depth + 1, fn) };
  return { ...values, [seg.key]: list.map((e, i) => (i === seg.index ? next : e)) };
}

/** Appends an empty entry to the list the last path segment names; a no-op at `count_max`. */
export function addEntry(values: AttrValues, path: EntryPath, def: AttributeDef): AttrValues {
  return updateAt(values, path, 0, (list) => (list.length >= (def.count_max ?? 1) ? list : [...list, newEntry(def)]));
}

/** Removes the entry at the last path segment (the caller enforces `count_min`). */
export function removeEntry(values: AttrValues, path: EntryPath): AttrValues {
  const last = path[path.length - 1];
  if (!last) return values;
  return updateAt(values, path, 0, (list) => list.filter((_, i) => i !== last.index));
}

/** Sets `v` of the entry at `path`. */
export function setValue(values: AttrValues, path: EntryPath, v: AttrScalar): AttrValues {
  const last = path[path.length - 1];
  if (!last) return values;
  return updateAt(values, path, 0, (list) => list.map((e, i) => (i === last.index ? { ...e, v } : e)));
}

/** The entry list at `path` (the entry at the last index is not needed: the whole list is returned). */
export function entriesAt(values: AttrValues, key: string, parents: EntryPath): AttrEntry[] {
  let level: AttrValues | undefined = values;
  for (const p of parents) level = level?.[p.key]?.[p.index]?.s;
  return level?.[key] ?? [];
}

/** Top-level keys the definition does not know (sorted); saving removes them. */
export function unknownKeys(defs: readonly AttributeDef[], values: Record<string, unknown>): string[] {
  const known = new Set(defs.map((d) => d.key));
  return Object.keys(values)
    .filter((k) => !known.has(k))
    .sort();
}

// ---------------------------------------------------------------- validation

const isBlank = (v: unknown): boolean => v === null || v === undefined || (typeof v === "string" && v.trim() === "");

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const INT = /^[+-]?\d+$/;
const DECIMAL = /^[+-]?(\d+\.?\d*|\.\d+)$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DATETIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$/;
const MAX_INT = Number.MAX_SAFE_INTEGER;

function validIPv4(s: string): boolean {
  const parts = s.split(".");
  return parts.length === 4 && parts.every((p) => /^\d{1,3}$/.test(p) && Number(p) <= 255);
}

function validIPv6(s: string): boolean {
  if (!/^[0-9a-fA-F:.]+$/.test(s) || s.includes(":::")) return false;
  const halves = s.split("::");
  if (halves.length > 2) return false;
  const groups = (h: string) => (h === "" ? [] : h.split(":"));
  const all = halves.flatMap(groups);
  let count = 0;
  for (let i = 0; i < all.length; i += 1) {
    const g = all[i];
    if (g.includes(".")) {
      if (i !== all.length - 1 || !validIPv4(g)) return false;
      count += 2;
    } else if (/^[0-9a-fA-F]{1,4}$/.test(g)) {
      count += 1;
    } else return false;
  }
  return halves.length === 2 ? count < 8 : count === 8;
}

function validIp(s: string): boolean {
  const [addr, prefix, ...rest] = s.split("/");
  if (rest.length > 0) return false;
  const v6 = addr.includes(":");
  if (!(v6 ? validIPv6(addr) : validIPv4(addr))) return false;
  if (prefix === undefined) return true;
  return /^\d{1,3}$/.test(prefix) && Number(prefix) <= (v6 ? 128 : 32);
}

function validUrl(s: string): boolean {
  try {
    const u = new URL(s);
    return (u.protocol === "http:" || u.protocol === "https:") && u.host !== "";
  } catch {
    return false;
  }
}

function validDate(s: string): boolean {
  if (!DATE.test(s)) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

function outOfRange(def: AttributeDef, n: number): boolean {
  const lo = def.options?.min;
  const hi = def.options?.max;
  return (lo != null && n < Number(lo)) || (hi != null && n > Number(hi));
}

/** Error code of a non-empty scalar, or null when it is fine. */
function scalarIssue(def: AttributeDef, raw: AttrScalar): ValidationCode | null {
  const t = def.type;
  if (t === "group") return null;
  const v = raw;
  if (t === "integer" || ID_TYPES.has(t)) {
    const s = typeof v === "number" ? String(v) : typeof v === "string" ? v.trim() : "";
    if (typeof v === "number" ? !Number.isInteger(v) : !INT.test(s)) return "bad_type";
    const n = Number(s);
    if (Math.abs(n) > MAX_INT) return "out_of_range";
    return t === "integer" && outOfRange(def, n) ? "out_of_range" : null;
  }
  if (t === "decimal" || t === "money") {
    const s = String(v).trim();
    if (!DECIMAL.test(s)) return "bad_type";
    return outOfRange(def, Number(s)) ? "out_of_range" : null;
  }
  if (typeof v !== "string") return "bad_type";
  const s = t === "text" || t === "textarea" ? v : v.trim();
  if ((t === "text" || t === "textarea") && s.trim() === "") return "bad_type";
  const limit = def.options?.max_length ?? DEFAULT_MAX_LENGTH[t];
  if (limit !== undefined && s.length > limit) return "too_long";
  switch (t) {
    case "email":
      return EMAIL.test(s) ? null : "bad_format";
    case "url":
      return validUrl(s) ? null : "bad_format";
    case "ip":
      return validIp(s) ? null : "bad_format";
    case "mac":
      return /^[0-9a-f]{12}$/.test(s.replace(/[:\-.\s]/g, "").toLowerCase()) ? null : "bad_format";
    case "date":
      return validDate(s) ? null : "bad_format";
    case "datetime":
      return DATETIME.test(s) && !Number.isNaN(new Date(`${s.replace(" ", "T")}Z`).getTime()) ? null : "bad_format";
    default:
      return null;
  }
}

type LevelResult = { kept: AttrValues; issues: Record<string, ValidationCode> };

/** One level of the D4 normalisation: keeps non-empty entries, drops empty ones, collects issues. */
function walkLevel(defs: readonly AttributeDef[], raw: AttrValues, prefix: string, keepBad: boolean, issues: Record<string, ValidationCode>): AttrValues {
  const out: AttrValues = {};
  for (const d of defs) {
    const path = `${prefix}${d.key}`;
    const entries = raw[d.key] ?? [];
    const kept: AttrEntry[] = [];
    let unusable = 0;
    entries.forEach((entry, i) => {
      const epath = `${path}[${i + 1}]`;
      const local: Record<string, ValidationCode> = {};
      let valueBad = false;
      if (d.type !== "group" && !isBlank(entry.v)) {
        const code = scalarIssue(d, entry.v);
        if (code) {
          valueBad = true;
          local[epath] = code;
        }
      }
      const subDefs = d.sub ?? [];
      const sub = subDefs.length > 0 ? walkLevel(subDefs, entry.s ?? {}, `${epath}.`, keepBad, local) : {};
      if (valueBad) {
        Object.assign(issues, local);
        unusable += 1;
        if (keepBad) kept.push(entry);
        return;
      }
      const empty = (d.type === "group" || isBlank(entry.v)) && Object.keys(sub).length === 0;
      if (empty) {
        // sub attributes of an entry that is not there cannot be "required"
        for (const [k, c] of Object.entries(local)) if (c !== "required" && c !== "too_few") issues[k] = c;
        if (d.required) {
          issues[epath] = "required";
          unusable += 1;
          if (keepBad) kept.push(entry);
        }
        return;
      }
      Object.assign(issues, local);
      kept.push(subDefs.length > 0 ? { v: entry.v, s: sub } : { v: entry.v });
    });
    if (kept.length + (keepBad ? 0 : unusable) > (d.count_max ?? 1)) issues[path] = "too_many";
    if (kept.length + (keepBad ? 0 : unusable) < (d.count_min ?? 0)) issues[path] = "too_few";
    if (kept.length > 0) out[d.key] = kept;
  }
  return out;
}

function walk(defs: readonly AttributeDef[], values: AttrValues, keepBad: boolean): LevelResult {
  const issues: Record<string, ValidationCode> = {};
  const kept = walkLevel(defs, values, "", keepBad, issues);
  return { kept, issues };
}

/** Issue per entry path (`NIC[1].IPAddress[2]`) as an i18n key `cmdb.validation.<code>`. */
export function validateValues(defs: readonly AttributeDef[], values: AttrValues): Record<string, string> {
  const { issues } = walk(defs, values, false);
  return Object.fromEntries(Object.entries(issues).map(([p, c]) => [p, `cmdb.validation.${c}`]));
}

/**
 * Drops empty entries like the backend: an empty entry of a `required` attribute stays (the server
 * reports `required`), empty entries of other attributes go, and unknown keys are left out.
 */
export function pruneEmpty(defs: readonly AttributeDef[], values: AttrValues): AttrValues {
  return walk(defs, values, true).kept;
}

// ---------------------------------------------------------------- field mapping

const KIND_BY_TYPE: Partial<Record<string, FieldKind>> = {
  text: "text",
  url: "text",
  email: "text",
  ip: "text",
  mac: "text",
  decimal: "text",
  money: "text",
  textarea: "textarea",
  integer: "number",
  date: "date",
  datetime: "datetime",
  catalog: "select",
  customer_user: "customer",
  customer_company: "company",
  ci_ref: "ci_ref",
  attachment: "select",
};

function optionsFor(def: AttributeDef, options: FormOptions): FieldOption[] {
  const rows = def.type === "catalog" ? (def.options?.catalog_class ? options.catalog?.[def.options.catalog_class] : undefined) : SELECT_TYPES.has(def.type) ? options[def.type as keyof FormOptions] : undefined;
  return ((rows ?? []) as Array<{ value: string; label: string }>).map((o) => ({ value: String(o.value), label: o.label }));
}

/** Field definition of one attribute entry; labels are translated with `t`/the definition. */
export function toFieldDef(def: AttributeDef, t: TFunction, options: FormOptions): FieldDef {
  const kind: FieldKind = KIND_BY_TYPE[def.type] ?? (SELECT_TYPES.has(def.type) ? "select" : "text");
  const out: FieldDef = { key: def.key, kind, label: def.name, required: def.required };
  if (def.help) out.help = def.help;
  if (kind === "select") {
    out.options = optionsFor(def, options);
    out.allowEmpty = true;
    out.placeholder = t("cmdb.form.select");
  }
  if (kind === "number") {
    out.step = 1;
    if (def.options?.min != null) out.min = Number(def.options.min);
    if (def.options?.max != null) out.max = Number(def.options.max);
  }
  if (kind === "text" || kind === "textarea") {
    const limit = def.options?.max_length ?? DEFAULT_MAX_LENGTH[def.type];
    if (limit !== undefined) out.maxLength = limit;
  }
  if (kind === "ci_ref") out.lookupClassKeys = def.options?.class_keys ?? [];
  return out;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Stored scalar -> field engine value (datetime: UTC -> wall time in `zone`). */
export function toFieldValue(def: AttributeDef, v: AttrScalar, zone?: string): FieldValue {
  if (v === null || v === undefined) return null;
  if (def.type === "datetime") {
    return toZonedInputValue(new Date(`${String(v).replace(" ", "T").slice(0, 19)}Z`), zone) || null;
  }
  if (def.type === "integer") return typeof v === "number" ? v : Number(v);
  if (ID_TYPES.has(def.type)) return String(v);
  return String(v);
}

/** Field engine value -> stored scalar (datetime: wall time in `zone` -> naive UTC seconds). */
export function fromFieldValue(def: AttributeDef, fv: FieldValue, zone?: string): AttrScalar {
  if (fv === null || fv === undefined || fv === "" || Array.isArray(fv) || typeof fv === "boolean") return null;
  if (def.type === "datetime") {
    const d = typeof fv === "string" ? fromZonedInputValue(fv, zone) : null;
    if (!d) return null;
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
  }
  if (def.type === "integer") return typeof fv === "number" ? fv : Number(fv);
  if (ID_TYPES.has(def.type)) {
    const n = Number(fv);
    return Number.isInteger(n) ? n : null;
  }
  return String(fv);
}
