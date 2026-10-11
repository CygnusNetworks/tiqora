/** Client-side checks of a settings draft; the server stays the authority (its 422 maps onto fields). */
import type { TFunction } from "i18next";
import type { JsonSchema } from "@/components/fields/jsonSchema";
import { isSafeLinkUrl } from "./linkUrl";

export const MAX_NOTE_CHARS = 5000;
export const MAX_LINKS = 12;
export const MAX_TITLE_CHARS = 200;

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** Characters as the server counts them (code points). */
export const charCount = (s: string): number => [...s].length;

/** Integer / number fields of a flat schema must be present and inside their bounds. */
export function validateSchemaValues(
  schema: JsonSchema,
  values: Record<string, unknown>,
  t: TFunction,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const [key, prop] of Object.entries(schema.properties ?? {})) {
    if (prop.type !== "integer" && prop.type !== "number") continue;
    const v = values[key];
    const min = prop.minimum;
    const max = prop.maximum;
    const ok = typeof v === "number" && Number.isFinite(v) && (min === undefined || v >= min) && (max === undefined || v <= max);
    if (ok) continue;
    errors[key] =
      min !== undefined && max !== undefined
        ? t("dashboards.settings.range", { min, max })
        : t("dashboards.settings.number");
  }
  return errors;
}

export function validateTicketList(value: Record<string, unknown>, t: TFunction): Record<string, string> {
  const errors: Record<string, string> = {};
  const limit = value.limit;
  if (typeof limit !== "number" || !Number.isInteger(limit) || limit < 1 || limit > 50) {
    errors.limit = t("dashboards.settings.range", { min: 1, max: 50 });
  }
  if (!Array.isArray(value.columns) || value.columns.length === 0) {
    errors.columns = t("dashboards.settings.ticketList.columnsMin");
  }
  return errors;
}

export function validateNotes(value: Record<string, unknown>, t: TFunction): Record<string, string> {
  const md = typeof value.markdown === "string" ? value.markdown : "";
  return charCount(md) > MAX_NOTE_CHARS
    ? { markdown: t("dashboards.settings.notes.tooLong", { max: MAX_NOTE_CHARS }) }
    : {};
}

export type LinkRow = { label: string; url: string };

export function linkRows(value: Record<string, unknown>): LinkRow[] {
  const items = Array.isArray(value.items) ? value.items : [];
  return items.map((i) => ({
    label: isObj(i) && typeof i.label === "string" ? i.label : "",
    url: isObj(i) && typeof i.url === "string" ? i.url : "",
  }));
}

/** A row with neither label nor address is an unused line: ignored, dropped on apply. */
const isBlankRow = (r: LinkRow) => r.label.trim() === "" && r.url === "";

export function validateLinks(value: Record<string, unknown>, t: TFunction): Record<string, string> {
  const errors: Record<string, string> = {};
  linkRows(value).forEach((row, i) => {
    if (isBlankRow(row)) return;
    if (row.label.trim() === "") errors[`items.${i}.label`] = t("dashboards.settings.links.labelMissing");
    if (row.url === "") errors[`items.${i}.url`] = t("dashboards.settings.links.urlMissing");
    else if (!isSafeLinkUrl(row.url) || charCount(row.url) > 500) errors[`items.${i}.url`] = t("dashboards.settings.links.urlNotAllowed");
  });
  return errors;
}

/** Client errors of a core widget's bespoke editor. */
export function validateCore(typeKey: string, value: Record<string, unknown>, t: TFunction): Record<string, string> {
  switch (typeKey) {
    case "core.ticket-list":
      return validateTicketList(value, t);
    case "core.notes":
      return validateNotes(value, t);
    case "core.links":
      return validateLinks(value, t);
    default:
      return {};
  }
}

/** What is stored on "Übernehmen": unused link rows are dropped, labels trimmed. */
export function finalizeSettings(typeKey: string, value: Record<string, unknown>): Record<string, unknown> {
  if (typeKey !== "core.links") return value;
  return {
    ...value,
    items: linkRows(value)
      .filter((r) => !isBlankRow(r))
      .map((r) => ({ label: r.label.trim(), url: r.url })),
  };
}
