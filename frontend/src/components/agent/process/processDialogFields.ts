/**
 * Pure helpers for building a dynamic form from an
 * ``ActivityDialogDetailOut`` (see ``tiqora.process.schemas``).
 *
 * Field-type dispatch is intentionally simple (documented simplification,
 * see ProcessManagement subtask 4 task notes):
 *  - "Queue" renders as a <select> populated from ``api.listQueues()`` (the
 *    same agent-accessible lookup used elsewhere in the app) since that
 *    list is cheap to fetch and already flattened by ``flattenQueues``.
 *  - "State" / "Priority" / "Owner" / "Responsible" fall back to a plain
 *    text input — there is no cheap *agent*-accessible lookup for these
 *    (the admin CRUD endpoints require admin capability, which a ticket
 *    agent submitting a process dialog does not necessarily have).
 *  - "Article" renders as a Subject input + Body textarea (the minimum
 *    Znuny expects for TicketArticleCreate-style transition actions).
 *  - Everything else (including all ``DynamicField_*`` fields) renders as
 *    a plain text input — no per-DynamicField-type widget dispatch.
 */

import type { ActivityDialogFieldOut } from "@/lib/api";

export const ARTICLE_FIELD_NAME = "Article";
export const QUEUE_FIELD_NAME = "Queue";

export type ArticleFieldValue = { Subject: string; Body: string };

export type FieldValues = Record<string, unknown>;

/** ``display === "1"`` marks a field required in Znuny's ActivityDialog config. */
export function isFieldRequired(field: ActivityDialogFieldOut): boolean {
  return field.display === "1";
}

function asArticleValue(defaultValue: unknown): ArticleFieldValue {
  if (defaultValue && typeof defaultValue === "object") {
    const v = defaultValue as Record<string, unknown>;
    return {
      Subject: typeof v.Subject === "string" ? v.Subject : "",
      Body: typeof v.Body === "string" ? v.Body : "",
    };
  }
  return { Subject: "", Body: "" };
}

/** Seed a field's initial form value from its ``default_value``. */
export function initialFieldValue(fieldName: string, field: ActivityDialogFieldOut): unknown {
  if (fieldName === ARTICLE_FIELD_NAME) {
    return asArticleValue(field.default_value);
  }
  if (typeof field.default_value === "string" || typeof field.default_value === "number") {
    return field.default_value;
  }
  return "";
}

/** Build the initial ``field_values`` map for a dialog's ``field_order``. */
export function buildInitialFieldValues(
  fieldOrder: string[],
  fields: Record<string, ActivityDialogFieldOut>,
): FieldValues {
  const out: FieldValues = {};
  for (const name of fieldOrder) {
    const field = fields[name];
    if (!field) continue;
    out[name] = initialFieldValue(name, field);
  }
  return out;
}

function isBlank(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value.trim() === "";
  if (typeof value === "object") {
    return Object.values(value as Record<string, unknown>).every(
      (v) => typeof v !== "string" || v.trim() === "",
    );
  }
  return false;
}

/** Names of required fields (per ``field_order``) whose value is still blank. */
export function missingRequiredFields(
  fieldOrder: string[],
  fields: Record<string, ActivityDialogFieldOut>,
  values: FieldValues,
): string[] {
  return fieldOrder.filter((name) => {
    const field = fields[name];
    if (!field || !isFieldRequired(field)) return false;
    return isBlank(values[name]);
  });
}
