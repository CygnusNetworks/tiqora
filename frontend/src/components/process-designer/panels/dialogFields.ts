// Pure helpers for the form panel: Znuny ActivityDialog config <-> editable model.
// Znuny stores every scalar as a string ("Display": "1"), so the model keeps strings and the
// builder writes strings back. Unknown keys of the stored config are preserved.
import type { CatalogField } from "@/lib/processDesignerApi";

export type DisplayMode = "0" | "1" | "2";
export type EditorKind = "select" | "date" | "datetime" | "checkbox" | "textarea" | "text" | "none";

export const INTERFACES = ["AgentInterface", "CustomerInterface"] as const;
export const CHANNELS = ["Internal", "Email", "Phone"] as const;
export type Channel = (typeof CHANNELS)[number];
export type TimeUnitsMode = "0" | "1" | "2";

export type ArticleSettings = { channel: Channel; visibleForCustomer: boolean; timeUnits: TimeUnitsMode };

export type FieldModel = {
  display: DisplayMode;
  defaultValue: string;
  short: string;
  long: string;
  article?: ArticleSettings;
};

export type DialogModel = {
  short: string;
  long: string;
  submitButton: string;
  submitAdvice: string;
  interfaces: string[];
  permission: string;
  requiredLock: boolean;
  order: string[];
  fields: Record<string, FieldModel>;
};

/** Fields whose Display "1" (shown, optional) is a Znuny config error (wave-1 D1). */
export const REQUIRED_DISPLAY_FIELDS = ["Queue", "State", "Lock", "Priority"] as const;
/** Fields the customer interface does not render. */
export const AGENT_ONLY_FIELDS = ["Owner", "Responsible", "Lock", "PendingTime", "Attachments", "CustomerUserID", "CustomerID"] as const;
/** Fields without a typed default value. */
const NO_DEFAULT = new Set(["Article", "Attachments", "PendingTime"]);

export type FieldHint = "queueRequired" | "customerHidden" | "hiddenNeedsDefault";

const str = (v: unknown): string => (v === undefined || v === null ? "" : String(v));
const rec = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

/** Which editor a catalogue field gets for its default value. */
export function editorKindOf(field: CatalogField | undefined, name: string): EditorKind {
  if (NO_DEFAULT.has(name)) return "none";
  if (!field) return "text";
  switch (field.kind) {
    case "Date":
      return "date";
    case "DateTime":
      return "datetime";
    case "Checkbox":
      return "checkbox";
    case "TextArea":
      return "textarea";
    case "Dropdown":
    case "Multiselect":
      return "select";
    default:
      return (field.options ?? []).length > 0 || field.kind === "select" ? "select" : "text";
  }
}

export function normalizeDisplay(v: unknown): DisplayMode {
  const s = str(v);
  return s === "1" || s === "2" ? s : "0";
}

export function readDialog(config: Record<string, unknown>): DialogModel {
  const fieldsRaw = rec(config.Fields);
  const stored = Array.isArray(config.FieldOrder) ? config.FieldOrder.map(str) : [];
  const order = [...stored.filter((n) => n in fieldsRaw), ...Object.keys(fieldsRaw).filter((n) => !stored.includes(n))];
  const ifaceRaw = config.Interface;
  const interfaces = (Array.isArray(ifaceRaw) ? ifaceRaw : ifaceRaw ? [ifaceRaw] : []).map(str).filter(Boolean);
  const fields: Record<string, FieldModel> = {};
  for (const name of order) {
    const f = rec(fieldsRaw[name]);
    const model: FieldModel = {
      display: normalizeDisplay(f.Display),
      defaultValue: str(f.DefaultValue),
      short: str(f.DescriptionShort),
      long: str(f.DescriptionLong),
    };
    if (name === "Article") {
      const c = rec(f.Config);
      const ch = str(c.CommunicationChannel);
      model.article = {
        channel: (CHANNELS as readonly string[]).includes(ch) ? (ch as Channel) : "Internal",
        visibleForCustomer: str(c.IsVisibleForCustomer) === "1",
        timeUnits: (["1", "2"].includes(str(c.TimeUnits)) ? str(c.TimeUnits) : "0") as TimeUnitsMode,
      };
    }
    fields[name] = model;
  }
  return {
    short: str(config.DescriptionShort),
    long: str(config.DescriptionLong),
    submitButton: str(config.SubmitButtonText),
    submitAdvice: str(config.SubmitAdviceText),
    interfaces,
    permission: str(config.Permission),
    requiredLock: str(config.RequiredLock) === "1",
    order,
    fields,
  };
}

/** Znuny config shape: strings only, `FieldOrder` and `Fields` in step, unknown keys kept. */
export function buildDialogConfig(base: Record<string, unknown>, m: DialogModel, accountTime: boolean): Record<string, unknown> {
  const oldFields = rec(base.Fields);
  const fields: Record<string, unknown> = {};
  for (const name of m.order) {
    const f = m.fields[name];
    if (!f) continue;
    const prev = rec(oldFields[name]);
    const out: Record<string, unknown> = {
      ...prev,
      DescriptionShort: f.short,
      DescriptionLong: f.long,
      DefaultValue: NO_DEFAULT.has(name) ? str(prev.DefaultValue) : f.defaultValue,
      Display: f.display,
    };
    if (name === "Article" && f.article) {
      out.Config = {
        ...rec(prev.Config),
        CommunicationChannel: f.article.channel,
        IsVisibleForCustomer: f.article.visibleForCustomer ? "1" : "0",
        ...(accountTime || "TimeUnits" in rec(prev.Config) ? { TimeUnits: f.article.timeUnits } : {}),
      };
    }
    fields[name] = out;
  }
  return {
    ...base,
    DescriptionShort: m.short,
    DescriptionLong: m.long,
    SubmitButtonText: m.submitButton,
    SubmitAdviceText: m.submitAdvice,
    Interface: m.interfaces,
    Permission: m.permission,
    RequiredLock: m.requiredLock ? "1" : "0",
    FieldOrder: m.order.filter((n) => n in fields),
    Fields: fields,
  };
}

export function newFieldModel(name: string): FieldModel {
  return {
    display: "1",
    defaultValue: "",
    short: "",
    long: "",
    ...(name === "Article" ? { article: { channel: "Internal" as Channel, visibleForCustomer: false, timeUnits: "0" as TimeUnitsMode } } : {}),
  };
}

export function fieldHints(name: string, f: FieldModel, interfaces: string[]): FieldHint[] {
  const out: FieldHint[] = [];
  if (f.display === "1" && (REQUIRED_DISPLAY_FIELDS as readonly string[]).includes(name)) out.push("queueRequired");
  if (f.display !== "0" && interfaces.includes("CustomerInterface") && (AGENT_ONLY_FIELDS as readonly string[]).includes(name))
    out.push("customerHidden");
  if (f.display === "0" && !NO_DEFAULT.has(name) && f.defaultValue.trim() === "") out.push("hiddenNeedsDefault");
  return out;
}
