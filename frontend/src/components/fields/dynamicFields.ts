import { fromZonedInputValue, toZonedInputValue } from "@/lib/timeZone";
import type { DynamicFieldDef } from "@/lib/phoneApi";
import type { FieldDef, FieldKind, FieldValue } from "./types";

/** Znuny dynamic-field values: `{name: [values]}`, what the ticket APIs take. */
export type DynamicFieldValues = Record<string, string[]>;

/** Znuny `field_type` -> field engine kind (unknown types fall back to text). */
export const DF_KIND: Record<string, FieldKind> = {
  Text: "text",
  TextArea: "textarea",
  Dropdown: "select",
  Multiselect: "multiselect",
  Checkbox: "boolean",
  Date: "date",
  DateTime: "datetime",
};

export function dynamicFieldToDef(f: DynamicFieldDef): FieldDef {
  const kind = DF_KIND[f.field_type] ?? "text";
  const def: FieldDef = { key: f.name, kind, label: f.label, required: f.required };
  if (kind === "select" || kind === "multiselect") {
    def.options = Object.entries(f.possible_values ?? {}).map(([value, label]) => ({ value, label }));
    def.optionsKey = `DynamicField_${f.name}`;
    def.allowEmpty = kind === "select";
  }
  return def;
}

const UTC_STAMP = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?$/;

/** Znuny list value(s) -> engine value. DateTime is stored as UTC, shown in `zone`. */
export function dfToFieldValue(kind: FieldKind, values: readonly unknown[], zone?: string): FieldValue {
  const list = values.map(String);
  const first = list[0] ?? "";
  switch (kind) {
    case "boolean":
      return first === "1";
    case "date":
      return first.slice(0, 10);
    case "datetime": {
      const m = UTC_STAMP.exec(first);
      if (!m) return "";
      const iso = `${m[1]}T${m[2]}:${m[3] ?? "00"}${m[4] ?? "Z"}`;
      return toZonedInputValue(new Date(iso), zone);
    }
    case "multiselect":
      return list;
    default:
      return first;
  }
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Engine value -> Znuny list value(s). DateTime wall time in `zone` becomes UTC like Znuny stores it. */
export function fieldValueToDf(kind: FieldKind, value: FieldValue, zone?: string): string[] {
  switch (kind) {
    case "boolean":
      return [value === true ? "1" : "0"];
    case "multiselect":
      return Array.isArray(value) ? value.filter((v) => v !== "") : [];
    case "date":
      return typeof value === "string" && value ? [`${value} 00:00:00`] : [];
    case "datetime": {
      const d = typeof value === "string" ? fromZonedInputValue(value, zone) : null;
      if (!d) return [];
      return [
        `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
          `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`,
      ];
    }
    default:
      return value === null || value === undefined || value === "" || Array.isArray(value) ? [] : [String(value)];
  }
}
