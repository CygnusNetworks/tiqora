/**
 * Filter state of the CMDB list and its mapping to the search API (`CiSearchIn`) and to the stored
 * part of a saved search (`CiSearchCriteria`). The structured part is always one `all` of
 * conditions in a fixed order (deployment, incident, attributes), so a saved search comes back as
 * the same search.
 */
import type { CiSearchCriteria, CiSearchIn } from "@/lib/cmdbApi";

export type AttrFilterRow = { field: string; op: string; value: unknown };

export type CiFilterState = {
  classId: number | null;
  q: string;
  /** Deployment state catalog ids. */
  deployment: number[];
  /** Incident functionalities (`operational` / `warning` / `incident`). */
  incident: string[];
  attr: AttrFilterRow[];
};

export type CiSort = { sort: CiSearchIn["sort"]; order: CiSearchIn["order"] };
export const PAGE_SIZE = 50;

type Cond = { field: string; op: string; value?: unknown };
type Filter = NonNullable<CiSearchIn["filter"]>;

/** Operators that take no value. */
export const VALUELESS_OPS: ReadonlySet<string> = new Set(["is_empty", "is_not_empty", "is_true", "is_false"]);

export const emptyFilterState = (): CiFilterState => ({ classId: null, q: "", deployment: [], incident: [], attr: [] });

/** Number of active narrowing criteria (class, text, states, valid attribute rows). */
export function activeFilterCount(s: CiFilterState): number {
  return (
    (s.classId != null ? 1 : 0) +
    (s.q.trim() ? 1 : 0) +
    (s.deployment.length ? 1 : 0) +
    (s.incident.length ? 1 : 0) +
    (s.classId != null ? validAttrRows(s.attr).length : 0)
  );
}

function validAttrRows(rows: AttrFilterRow[]): AttrFilterRow[] {
  return rows.filter((r) => {
    if (!r.field || !r.op) return false;
    if (VALUELESS_OPS.has(r.op)) return true;
    return r.value !== undefined && r.value !== null && r.value !== "";
  });
}

function buildFilter(s: CiFilterState): Filter | null {
  const all: Cond[] = [];
  if (s.deployment.length) all.push({ field: "deployment_state", op: "in", value: s.deployment.map(String) });
  if (s.incident.length) all.push({ field: "incident_functionality", op: "in", value: [...s.incident] });
  // attribute fields exist for exactly one class
  if (s.classId != null) {
    for (const r of validAttrRows(s.attr)) {
      all.push(VALUELESS_OPS.has(r.op) ? { field: r.field, op: r.op } : { field: r.field, op: r.op, value: r.value });
    }
  }
  return all.length ? ({ version: 1, where: { all } } as unknown as Filter) : null;
}

export function toCriteria(s: CiFilterState, sort: CiSort): CiSearchCriteria {
  return {
    class_ids: s.classId != null ? [s.classId] : [],
    q: s.q.trim() || null,
    filter: buildFilter(s),
    sort: sort.sort,
    order: sort.order,
  };
}

/** `page` is 1-based. */
export function toSearchBody(s: CiFilterState, page: number, sort: CiSort, pageSize = PAGE_SIZE): CiSearchIn {
  const c = toCriteria(s, sort);
  return { ...c, offset: (Math.max(1, page) - 1) * pageSize, limit: pageSize } as CiSearchIn;
}

const asArray = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** The state a stored criteria stands for; conditions the list cannot show are ignored. */
export function fromSavedCriteria(criteria: CiSearchCriteria): CiFilterState {
  const state = emptyFilterState();
  state.classId = criteria.class_ids?.length === 1 ? criteria.class_ids[0] : null;
  state.q = criteria.q ?? "";
  const where = criteria.filter?.where as { all?: Cond[] } | null | undefined;
  for (const c of asArray(where?.all) as Cond[]) {
    if (!c || typeof c.field !== "string") continue;
    if (c.field === "deployment_state" && c.op === "in") {
      state.deployment = asArray(c.value)
        .map((v) => Number(v))
        .filter((n) => Number.isInteger(n));
    } else if (c.field === "incident_functionality" && c.op === "in") {
      state.incident = asArray(c.value).map(String);
    } else if (c.field.startsWith("attr.") && state.classId != null) {
      state.attr.push({ field: c.field, op: c.op, value: c.value });
    }
  }
  return state;
}

/** Catalog classes behind the two state pickers (`seed.py` on the server). */
export const DEPLOYMENT_CATALOG = "ITSM::ConfigItem::DeploymentState";
export const INCIDENT_CATALOG = "ITSM::Core::IncidentState";

/** URL search parameters of the list route. */
export type CmdbListSearch = {
  class?: number;
  q?: string;
  saved?: number;
  page?: number;
  sort?: CiSort["sort"];
  order?: CiSort["order"];
};

const SORTS: ReadonlyArray<CiSort["sort"]> = ["number", "name", "class", "changed_at", "deployment_state", "incident_state"];

/** Router `validateSearch`: the URL is user input, so numbers are parsed defensively. */
export function validateCmdbListSearch(s: Record<string, unknown>): CmdbListSearch {
  const posInt = (v: unknown): number | undefined => {
    const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
    return Number.isInteger(n) && n > 0 ? n : undefined;
  };
  const out: CmdbListSearch = {};
  const cls = posInt(s.class);
  if (cls !== undefined) out.class = cls;
  // a search text of only digits is parsed to a number by the router's default codec
  if (typeof s.q === "string" && s.q !== "") out.q = s.q;
  else if (typeof s.q === "number") out.q = String(s.q);
  const saved = posInt(s.saved);
  if (saved !== undefined) out.saved = saved;
  const page = posInt(s.page);
  if (page !== undefined && page > 1) out.page = page;
  if (typeof s.sort === "string" && (SORTS as readonly string[]).includes(s.sort)) out.sort = s.sort as CiSort["sort"];
  if (s.order === "asc" || s.order === "desc") out.order = s.order;
  return out;
}

