import type { TFunction } from "i18next";
import type { FieldDef, FieldErrors, FieldValue, FieldValues } from "./types";

/** Blank text, empty list, null and undefined are empty; 0 and false are values. */
export function isEmptyFieldValue(value: FieldValue | undefined): boolean {
  if (value === undefined || value === null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

export function validateFieldValues(fields: FieldDef[], values: FieldValues, t: TFunction): FieldErrors {
  const errors: FieldErrors = {};
  for (const def of fields) {
    if (def.readOnly) continue;
    const value = values[def.key];
    if (def.required) {
      // Znuny: a required checkbox must be ticked.
      const missing = def.kind === "boolean" ? value !== true : isEmptyFieldValue(value);
      if (missing) {
        errors[def.key] = t("fields.required");
        continue;
      }
    }
    if (def.kind === "number" && value !== undefined && value !== null && value !== "") {
      const n = typeof value === "number" ? value : Number(value);
      if (!Number.isFinite(n) || (def.min !== undefined && n < def.min) || (def.max !== undefined && n > def.max)) {
        errors[def.key] = t("fields.invalidNumber");
        continue;
      }
    }
    if (def.maxLength !== undefined && typeof value === "string" && value.length > def.maxLength) {
      errors[def.key] = t("fields.tooLong");
    }
  }
  return errors;
}
