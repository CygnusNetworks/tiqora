// Pure helpers for the transition panel: Znuny transition `Condition` <-> editable groups.
// Znuny stores `{Condition: {"1": {Type: "and", Fields: {Name: {Type: "String", Match: "x"}}}},
// ConditionLinking: "and"}` (AdminProcessManagementTransition.pm:518-552). Group keys must be the
// consecutive strings "1".."n"; field names are unique inside a group because `Fields` is a hash.

export const LINKINGS = ["and", "or", "xor"] as const;
export type Linking = (typeof LINKINGS)[number];

/** Condition types the designer offers (same list as the backend catalogue, D2). */
export const CONDITION_TYPES = [
  "Module",
  "Regexp",
  "String",
  "Equal",
  "NotEqual",
  "Contains",
  "NotContains",
  "GreaterThan",
  "GreaterThanOrEqual",
  "LessThan",
  "LessThanOrEqual",
] as const;

export type ConditionRow = {
  field: string;
  type: string;
  /** Znuny's `Match`; for `Module` the Perl module path. */
  match: string;
  /** A stored `Match` that is not a scalar (Znuny accepts arrays); kept verbatim until edited. */
  matchRaw?: unknown;
};
export type ConditionGroup = { linking: Linking; rows: ConditionRow[] };
export type ConditionModel = { linking: Linking; groups: ConditionGroup[] };

const rec = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

export function normalizeLinking(v: unknown): Linking {
  return v === "or" || v === "xor" ? v : "and";
}

/** Reads `Condition`/`ConditionLinking` of a transition config; tolerant of missing keys. */
export function readCondition(config: Record<string, unknown>): ConditionModel {
  const cond = rec(config.Condition);
  const groups = Object.keys(cond)
    .sort((a, b) => Number(a) - Number(b))
    .map((key): ConditionGroup => {
      const g = rec(cond[key]);
      const fields = rec(g.Fields);
      return {
        linking: normalizeLinking(g.Type),
        rows: Object.keys(fields).map((field): ConditionRow => {
          const f = rec(fields[field]);
          const m = f.Match;
          const scalar = typeof m === "string" || typeof m === "number";
          const type = typeof f.Type === "string" ? f.Type : "String";
          return {
            field,
            type,
            match: scalar ? String(m) : "",
            ...(m !== undefined && m !== null && !scalar ? { matchRaw: m } : {}),
          };
        }),
      };
    });
  return { linking: normalizeLinking(config.ConditionLinking), groups };
}

/** Group keys renumbered "1".."n" (Znuny re-reads them in this form). */
export function buildCondition(model: ConditionModel): Record<string, unknown> {
  return Object.fromEntries(
    model.groups.map((g, i) => [
      String(i + 1),
      {
        Type: g.linking,
        Fields: Object.fromEntries(
          g.rows.map((r) => [r.field, { Type: r.type, Match: r.matchRaw !== undefined ? r.matchRaw : r.match }]),
        ),
      },
    ]),
  );
}

/** Config with the condition replaced; every other stored key is preserved. */
export function buildTransitionConfig(config: Record<string, unknown>, model: ConditionModel): Record<string, unknown> {
  return { ...config, Condition: buildCondition(model), ConditionLinking: model.linking };
}

export function removeGroup(model: ConditionModel, index: number): ConditionModel {
  return { ...model, groups: model.groups.filter((_, i) => i !== index) };
}

/** Field names that occur more than once in a group (the hash would keep only one). */
export function duplicateFields(group: ConditionGroup): string[] {
  const seen = new Set<string>();
  const dup = new Set<string>();
  for (const r of group.rows) {
    if (!r.field) continue;
    if (seen.has(r.field)) dup.add(r.field);
    seen.add(r.field);
  }
  return [...dup];
}

/** Why a `Regexp` match is unusable in the browser, else null. */
export function regexpProblem(match: string): "empty" | "invalid" | null {
  if (match === "") return "empty";
  try {
    new RegExp(match);
    return null;
  } catch {
    return "invalid";
  }
}

/** A `Module` row whose path is not registered in Tiqora: kept, but Tiqora never matches it. */
export function isUnknownModule(row: ConditionRow, modules: readonly string[]): boolean {
  return row.type === "Module" && row.match !== "" && !modules.includes(row.match);
}

/** True when saving would write something the designer knows to be broken. */
export function hasBlockingProblem(model: ConditionModel): boolean {
  return model.groups.some(
    (g) =>
      duplicateFields(g).length > 0 ||
      g.rows.some(
        (r) => !r.field || (r.type === "Regexp" && r.matchRaw === undefined && regexpProblem(r.match) === "invalid"),
      ),
  );
}
