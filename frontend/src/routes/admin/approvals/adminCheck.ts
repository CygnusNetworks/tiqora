import type { TFunction } from "i18next";

export type CheckMessage = {
  /** Zero-based step the message belongs to, or null for a message about the whole definition. */
  step: number | null;
  text: string;
};

const FIELDS = new Set(["name", "stage", "rule", "quorum", "approvers", "due_hours", "on_timeout", "escalation"]);

/**
 * Turns a code of `POST /admin/approvals/templates/validate` (`invalid_definition:<path>`,
 * `unknown_resolver:<key>`, `invalid_ref:<kind>:<ref>`, `missing_user:<id>`, …) into a plain sentence
 * and, where the path names one, the step it belongs to.
 */
export function describeCheck(raw: string, t: TFunction): CheckMessage {
  const [code, ...rest] = raw.split(":");
  const arg = rest.join(":");
  if (code === "invalid_definition") {
    const parts = arg.split(".");
    const step = parts[0] === "steps" && /^\d+$/.test(parts[1] ?? "") ? Number(parts[1]) : null;
    const field = step !== null ? parts[2] : undefined;
    if (field && FIELDS.has(field)) return { step, text: t(`approvals.admin.templates.check.field.${field}`) };
    return { step, text: t("approvals.admin.templates.check.invalid_definition", { path: arg || "-" }) };
  }
  const known = [
    "unknown_resolver",
    "invalid_ref",
    "customer_entry_not_allowed",
    "unknown_subject_type",
    "missing_user",
    "missing_group",
    "missing_role",
    "missing_list",
    "missing_customer",
  ];
  if (known.includes(code)) return { step: null, text: t(`approvals.admin.templates.check.${code}`, { arg }) };
  return { step: null, text: raw };
}
