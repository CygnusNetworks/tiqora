import type { TFunction } from "i18next";
import type { FieldDef, FieldKind, FieldOption, FieldValue, FieldValues } from "./types";

/** The JSON-schema subset the settings sections use (see Design D2/D5). */
export type JsonSchema = {
  type?: string | string[];
  enum?: unknown[];
  const?: unknown;
  format?: string;
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  maxLength?: number;
  items?: JsonSchema;
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  $ref?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  $defs?: Record<string, JsonSchema>;
  writeOnly?: boolean;
  "x-widget"?: "textarea";
};

type Resolved = { schema: JsonSchema; nullable: boolean; enumValues?: { value: string; title?: string }[] };

function deref(root: JsonSchema, s: JsonSchema): JsonSchema | null {
  if (!s.$ref) return s;
  const m = /^#\/\$defs\/(.+)$/.exec(s.$ref);
  const target = m ? root.$defs?.[m[1]] : undefined;
  if (!target || target.$ref) return null;
  // Keep the property's own title/default/description over the definition's.
  return { ...target, ...(s.default !== undefined ? { default: s.default } : {}) };
}

function enumOf(s: JsonSchema): { value: string; title?: string }[] | undefined {
  if (s.enum && s.enum.every((v) => typeof v === "string")) {
    return (s.enum as string[]).map((value) => ({ value }));
  }
  const alts = s.anyOf ?? s.oneOf;
  if (alts && alts.length > 0 && alts.every((a) => typeof a.const === "string")) {
    return alts.map((a) => ({ value: a.const as string, title: a.title }));
  }
  return undefined;
}

function resolve(root: JsonSchema, raw: JsonSchema): Resolved | null {
  const first = deref(root, raw);
  if (!first) return null;
  let s = first;
  let nullable = false;
  const alts = s.anyOf ?? s.oneOf;
  if (alts && alts.some((a) => a.type === "null")) {
    nullable = true;
    const rest = alts.filter((a) => a.type !== "null");
    if (rest.length !== 1) return null;
    const inner = deref(root, rest[0]);
    if (!inner) return null;
    s = { ...inner, default: s.default, title: s.title ?? inner.title, description: s.description };
  }
  return { schema: s, nullable, enumValues: enumOf(s) };
}

function kindOf(r: Resolved): FieldKind | null {
  const { schema: s } = r;
  if (r.enumValues) return "select";
  const type = Array.isArray(s.type) ? s.type[0] : s.type;
  switch (type) {
    case "boolean":
      return "boolean";
    case "integer":
    case "number":
      return "number";
    case "string":
      if (s.format === "password") return "secret";
      if (s.format === "date") return "date";
      if (s.format === "date-time") return "datetime";
      return s["x-widget"] === "textarea" ? "textarea" : "text";
    case "array": {
      if (!s.items) return null;
      const items = { schema: s.items, nullable: false, enumValues: enumOf(s.items) };
      if (items.enumValues) return "multiselect";
      return items.schema.type === "string" ? "tags" : null;
    }
    default:
      return null;
  }
}

export function schemaToFieldDefs(
  schema: JsonSchema,
  opts: { t: TFunction; i18nPrefix?: string; secretsSet?: string[] },
): { fields: FieldDef[]; unsupported: string[] } {
  const { t, i18nPrefix, secretsSet = [] } = opts;
  const fields: FieldDef[] = [];
  const unsupported: string[] = [];
  const required = new Set(schema.required ?? []);
  const tr = (key: string, fallback: string) => String(t(key, { defaultValue: fallback }));

  for (const [key, raw] of Object.entries(schema.properties ?? {})) {
    const r = resolve(schema, raw);
    const kind = r ? kindOf(r) : null;
    if (!r || !kind) {
      unsupported.push(key);
      continue;
    }
    const s = r.schema;
    const title = raw.title ?? s.title ?? key;
    const base = i18nPrefix ? `${i18nPrefix}.fields.${key}` : null;
    const def: FieldDef = {
      key,
      kind,
      label: base ? tr(`${base}.label`, title) : title,
      required: required.has(key) || undefined,
    };
    const help = base ? tr(`${base}.help`, raw.description ?? s.description ?? "") : (raw.description ?? "");
    if (help) def.help = help;
    const enumValues = r.enumValues ?? (s.items ? enumOf(s.items) : undefined);
    if (enumValues) {
      def.options = enumValues.map<FieldOption>((e) => ({
        value: e.value,
        label: base ? tr(`${base}.options.${e.value}`, e.title ?? e.value) : (e.title ?? e.value),
      }));
      if (kind === "select" && r.nullable) def.allowEmpty = true;
    }
    if (kind === "number") {
      if (s.minimum !== undefined) def.min = s.minimum;
      if (s.maximum !== undefined) def.max = s.maximum;
      if (s.type === "integer") def.step = 1;
    }
    if (s.maxLength !== undefined) def.maxLength = s.maxLength;
    if (kind === "secret") def.secretSet = secretsSet.includes(key);
    fields.push(def);
  }
  return { fields, unsupported };
}

export function schemaDefaults(schema: JsonSchema): FieldValues {
  const out: FieldValues = {};
  for (const [key, raw] of Object.entries(schema.properties ?? {})) {
    const r = resolve(schema, raw);
    const kind = r ? kindOf(r) : null;
    if (!r || !kind) continue;
    if (kind === "secret") {
      out[key] = null;
      continue;
    }
    const d = raw.default ?? r.schema.default;
    if (d !== undefined) out[key] = d as FieldValue;
    else if (kind === "boolean") out[key] = false;
    else if (kind === "tags" || kind === "multiselect") out[key] = [];
    else out[key] = null;
  }
  return out;
}

/** Properties that hold secrets (`format: "password"`). */
export function secretKeys(schema: JsonSchema): string[] {
  return Object.entries(schema.properties ?? {})
    .filter(([, raw]) => {
      const r = resolve(schema, raw);
      return r && kindOf(r) === "secret";
    })
    .map(([key]) => key);
}
