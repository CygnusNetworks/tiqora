// Pure model of the action panel: a TransitionAction entity config is
// {Module: "Kernel::System::ProcessManagement::TransitionAction::<Name>", Config: {<Znuny key>: value, ...}}.
// The catalogue's parameter specs become typed rows; every other key stays editable as a raw row,
// so nothing of an imported Znuny config is lost.
import type { FieldDef, FieldOption } from "@/components/fields";
import type { DesignerCatalog } from "@/lib/processDesignerApi";

export type CatalogAction = DesignerCatalog["actions"][number];
type CatalogParam = CatalogAction["params"][number];

export const MODULE_PREFIX = "Kernel::System::ProcessManagement::TransitionAction::";
/** Written by the catalogue as the implicit `UserID` parameter; shown under "Weitere Einstellungen". */
export const USER_ID_KEY = "UserID";

export type ActionGroupId = "ticket" | "article" | "dynamicField" | "calendar" | "other";
export const ACTION_GROUPS: ActionGroupId[] = ["ticket", "article", "dynamicField", "calendar", "other"];

export function shortModule(module: string): string {
  const i = module.lastIndexOf("::");
  return i >= 0 ? module.slice(i + 2) : module;
}

export function groupOfAction(name: string): ActionGroupId {
  if (name.includes("Article")) return "article";
  if (name.startsWith("DynamicField")) return "dynamicField";
  if (name.startsWith("Appointment")) return "calendar";
  if (name.startsWith("Ticket")) return "ticket";
  return "other";
}

/** A typed parameter: one key, or several alternative keys of which exactly one is used ("Name oder ID"). */
export type ParamGroup = {
  id: string;
  keys: string[];
  kind: string;
  required: boolean;
  listValue: boolean;
};

export function paramGroups(action: CatalogAction | undefined): ParamGroup[] {
  const out: ParamGroup[] = [];
  const seen = new Set<string>();
  for (const p of action?.params ?? ([] as CatalogParam[])) {
    if (p.name === USER_ID_KEY) continue;
    const keys = p.one_of.length > 1 ? p.one_of : [p.name];
    const id = keys.join("|");
    if (seen.has(id)) continue;
    seen.add(id);
    out.push({ id, keys, kind: p.kind, required: p.required, listValue: p.list_value });
  }
  return out;
}

export type RawRow = { key: string; value: string; locked: boolean };
export type ActionFormState = {
  /** group id -> the alternative key in use and its value (text, smart tags allowed). */
  fields: Record<string, { key: string; value: string }>;
  raw: RawRow[];
  userId: string;
};

const isScalar = (v: unknown): v is string | number | boolean =>
  typeof v === "string" || typeof v === "number" || typeof v === "boolean";
const asConfig = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

export function moduleOf(config: Record<string, unknown>): string {
  return typeof config.Module === "string" ? config.Module : "";
}

export function readActionConfig(action: CatalogAction | undefined, config: Record<string, unknown>): ActionFormState {
  const cfg = asConfig(config.Config);
  const used = new Set<string>([USER_ID_KEY]);
  const fields: ActionFormState["fields"] = {};
  for (const g of paramGroups(action)) {
    const present = g.keys.find((k) => isScalar(cfg[k]) && String(cfg[k]) !== "");
    const key = present ?? g.keys[0];
    fields[g.id] = { key, value: present ? String(cfg[present]) : "" };
    if (present) used.add(present);
  }
  const raw: RawRow[] = Object.keys(cfg)
    .filter((k) => !used.has(k))
    .map((k) => {
      const v = cfg[k];
      return isScalar(v) ? { key: k, value: String(v), locked: false } : { key: k, value: JSON.stringify(v), locked: true };
    });
  const uid = cfg[USER_ID_KEY];
  return { fields, raw, userId: isScalar(uid) ? String(uid) : "" };
}

/** Keeps the stored type (number vs. string) when the text did not change. */
function typed(original: unknown, value: string): string | number | boolean {
  return isScalar(original) && String(original) === value ? original : value;
}

export function buildActionConfig(
  action: CatalogAction | undefined,
  base: Record<string, unknown>,
  state: ActionFormState,
): Record<string, unknown> {
  const old = asConfig(base.Config);
  const next: Record<string, unknown> = {};
  for (const g of paramGroups(action)) {
    const f = state.fields[g.id];
    const v = f?.value.trim() ?? "";
    if (f && v !== "") next[f.key] = typed(old[f.key], v);
  }
  for (const r of state.raw) {
    const k = r.key.trim();
    if (!k || k in next || k === USER_ID_KEY) continue;
    next[k] = r.locked ? old[k] : typed(old[k], r.value);
  }
  const uid = state.userId.trim();
  if (uid !== "") {
    // numeric ids are stored as numbers (Znuny's own exports), smart tags stay text
    const kept = typed(old[USER_ID_KEY], uid);
    next[USER_ID_KEY] = kept !== uid ? kept : /^\d+$/.test(uid) ? Number(uid) : uid;
  }
  return { ...base, Config: next };
}

/** Catalogue field that supplies the suggestions of a parameter kind. */
const KIND_FIELD: Record<string, string> = {
  queue: "Queue",
  state: "State",
  priority: "Priority",
  type: "Type",
  service: "Service",
  sla: "SLA",
  user: "Owner",
  lock: "Lock",
};

export function paramOptions(kind: string, catalog: DesignerCatalog): FieldOption[] {
  if (kind === "bool") return [{ value: "1", label: "1" }, { value: "0", label: "0" }];
  if (kind === "dynamic_field") {
    return catalog.fields
      .filter((f) => f.name.startsWith("DynamicField_"))
      .map((f) => ({ value: f.name.slice("DynamicField_".length), label: f.label }));
  }
  const name = KIND_FIELD[kind];
  const field = name ? catalog.fields.find((f) => f.name === name) : undefined;
  return (field?.options ?? []).map((o) => ({ value: o.value, label: o.label }));
}

/**
 * `FieldDef`s for the typed parameters (key = group id). Every control is free text with suggestions,
 * because any value may be a smart tag such as `<OTRS_TICKET_Queue>`.
 */
export function actionFieldDefs(
  action: CatalogAction | undefined,
  catalog: DesignerCatalog,
  labelOf: (key: string) => string = (k) => k,
): FieldDef[] {
  return paramGroups(action).map((g) => ({
    key: g.id,
    kind: g.kind === "textarea" ? "textarea" : "text",
    label: g.keys.map(labelOf).join(" / "),
    required: g.required,
    options: paramOptions(g.kind, catalog),
  }));
}
