import type { TFunction } from "i18next";
import { dfToFieldValue, fieldValueToDf } from "@/components/fields/dynamicFields";
import type { FieldDef, FieldKind, FieldValue, FieldValues } from "@/components/fields/types";
import { isEmptyFieldValue } from "@/components/fields/validate";
import { fromZonedInputValue } from "@/lib/timeZone";
import type { ProcessDialogFieldData, ProcessDialogFormData } from "@/lib/processApi";

export const ARTICLE_SUBJECT_KEY = "Article.Subject";
export const ARTICLE_BODY_KEY = "Article.Body";
export const CUSTOMER_ID_KEY = "CustomerID";
const CUSTOMER_USER_KEY = "CustomerUserID";
const DF_PREFIX = "DynamicField_";

/** DF label, or the translated ticket attribute (the server sends the canonical name). */
export function processLabel(field: ProcessDialogFieldData, t: TFunction): string {
  if (field.name.startsWith(DF_PREFIX)) return field.label;
  return t(`process.field.${field.name}`, { defaultValue: field.label });
}

function optionLabel(field: ProcessDialogFieldData, value: string, label: string, t: TFunction): string {
  if (field.name === "Lock" && (value === "lock" || value === "unlock")) return t(`process.lock.${value}`);
  return label;
}

const ENGINE_KIND: Partial<Record<ProcessDialogFieldData["kind"], FieldKind>> = {
  text: "text",
  textarea: "textarea",
  select: "select",
  multiselect: "multiselect",
  boolean: "boolean",
  date: "date",
  datetime: "datetime",
  pending_time: "datetime",
};

export function processFieldDefs(form: ProcessDialogFormData, t: TFunction): FieldDef[] {
  const defs: FieldDef[] = [];
  for (const field of form.fields) {
    const required = field.required;
    const help = field.description_long || field.description_short || undefined;
    if (field.kind === "article") {
      defs.push(
        { key: ARTICLE_SUBJECT_KEY, kind: "text", label: t("process.field.ArticleSubject"), required },
        { key: ARTICLE_BODY_KEY, kind: "textarea", label: t("process.field.ArticleBody"), required, help },
      );
    } else if (field.kind === "customer") {
      defs.push(
        { key: CUSTOMER_USER_KEY, kind: "text", label: t("process.field.CustomerUserID"), required, help },
        { key: CUSTOMER_ID_KEY, kind: "text", label: t("process.field.CustomerID") },
      );
    } else if (field.kind === "unsupported") {
      defs.push({
        key: field.name,
        kind: "text",
        label: processLabel(field, t),
        readOnly: true,
        help: t("process.form.unsupported"),
      });
    } else {
      const kind = ENGINE_KIND[field.kind] ?? "text";
      const def: FieldDef = { key: field.name, kind, label: processLabel(field, t), required, help };
      if (kind === "select" || kind === "multiselect") {
        def.options = (field.options ?? []).map((o) => ({
          value: o.value,
          label: optionLabel(field, o.value, o.label, t),
        }));
        def.allowEmpty = kind === "select" && !required;
      }
      defs.push(def);
    }
  }
  return defs;
}

function asText(v: unknown): string {
  return typeof v === "string" || typeof v === "number" ? String(v) : "";
}

export function initialProcessValues(form: ProcessDialogFormData): FieldValues {
  const out: FieldValues = {};
  for (const field of form.fields) {
    const d = field.default;
    switch (field.kind) {
      case "article": {
        const a = d && typeof d === "object" ? (d as Record<string, unknown>) : {};
        out[ARTICLE_SUBJECT_KEY] = asText(a.Subject);
        out[ARTICLE_BODY_KEY] = asText(a.Body);
        break;
      }
      case "customer":
        out[CUSTOMER_USER_KEY] = asText(d);
        out[CUSTOMER_ID_KEY] = "";
        break;
      case "unsupported":
        break;
      case "boolean":
        out[field.name] = d === true || d === 1 || d === "1";
        break;
      case "multiselect":
        out[field.name] = Array.isArray(d) ? d.map(String) : d === null || d === undefined || d === "" ? [] : [String(d)];
        break;
      case "date":
        out[field.name] = asText(d).slice(0, 10);
        break;
      case "datetime":
      case "pending_time":
        out[field.name] = d === null || d === undefined || d === "" ? "" : dfToFieldValue("datetime", [d]);
        break;
      default:
        out[field.name] = asText(d);
    }
  }
  return out;
}

/** Engine values -> `field_values` of the API (article split, UTC ISO instants, DF lists). Empty ticket attributes are left out (Znuny skips them); an empty dynamic field is sent as `[]` and clears it. */
export function toSubmitValues(form: ProcessDialogFormData, values: FieldValues, zone?: string): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const put = (key: string, value: FieldValue | undefined) => {
    if (!isEmptyFieldValue(value)) out[key] = value;
  };
  for (const field of form.fields) {
    const v = values[field.name];
    switch (field.kind) {
      case "article":
        put("Subject", values[ARTICLE_SUBJECT_KEY]);
        put("Body", values[ARTICLE_BODY_KEY]);
        break;
      case "customer":
        put(CUSTOMER_USER_KEY, values[CUSTOMER_USER_KEY]);
        put(CUSTOMER_ID_KEY, values[CUSTOMER_ID_KEY]);
        break;
      case "unsupported":
        break;
      case "pending_time": {
        const d = typeof v === "string" ? fromZonedInputValue(v, zone) : null;
        if (d) out[field.name] = d.toISOString();
        break;
      }
      default: {
        if (field.name.startsWith(DF_PREFIX)) {
          // An empty list is sent on purpose: Znuny's ValueSet with an empty value clears the field
          // (the form shows the stored value, so empty means the user cleared it).
          out[field.name] = fieldValueToDf(ENGINE_KIND[field.kind] ?? "text", v ?? null, zone);
        } else if (field.kind === "datetime") {
          const d = typeof v === "string" ? fromZonedInputValue(v, zone) : null;
          if (d) out[field.name] = d.toISOString();
        } else if (field.kind === "boolean") {
          out[field.name] = v === true ? "1" : "0";
        } else if (field.kind === "multiselect") {
          if (Array.isArray(v) && v.length > 0) out[field.name] = v;
        } else if (!isEmptyFieldValue(v)) {
          out[field.name] = String(v);
        }
      }
    }
  }
  return out;
}
