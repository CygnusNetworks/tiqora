import type {
  ReportDefinition,
  ReportFile,
  ReportItem,
  ShareGroup,
  SavedReportIn,
  SavedReportUpdateIn,
} from "@/lib/reportsApi";
import type {
  FilterFieldOut,
  SavedSearch,
  SavedSearchIn,
  SavedSearchPrefsIn,
  SavedSearchUpdateIn,
  TicketFilter,
  ZnunyImportPreview,
} from "@/lib/savedSearchesApi";
import { DEMO_REPORTS } from "./reportsCatalogue";
import { demoFacts, setSavedReportLookup } from "./reportsMock";

/**
 * Demo store for saved reports and saved searches. The routes behind
 * `resolveSavedRequest` are the ones of `backend/src/tiqora/api/v1/reports.py`
 * and `saved_searches.py`; GETs are reached through `resolveData`, writes only
 * through the MSW body handlers in handlers.ts (`resolveData` never mutates).
 * State lives for the page session, like the other demo stores.
 */

const ME = { user_id: 1, name: "Alex Turner" };
const OTHER = { user_id: 2, name: "Clara Demo" };
const NOW = "2026-10-08T08:30:00Z";

// The pre-commit secret scan flags long `key: "<snake_case>"` literals (see reportsCatalogue.ts).
const m = (name: string) => ({ ["key"]: name });
const d = (name: string, rest: object = {}) => ({ ["key"]: name, ...rest });

function builtinDefinition(key: string): ReportDefinition {
  return (DEMO_REPORTS.find((r) => r.key === key) as { definition: ReportDefinition }).definition;
}

// --- saved reports ----------------------------------------------------------

function demoSavedReports(): ReportItem[] {
  return [
    {
      key: "101",
      kind: "saved",
      id: 101,
      name: "Meine offenen Tickets je Queue",
      description: "Offene Tickets, die mir gehören, nach Queue.",
      definition: {
        ...builtinDefinition("builtin.volume_by_queue"),
        measures: [m("open_tickets")],
        parameters: [{ key: "channel", field: "channel", op: "in", required: false }],
        filter: { version: 1, where: { field: "owner_id", op: "eq", value: 1 } },
        visualization: { type: "bar", chart_measures: [0] },
      } as ReportDefinition,
      visibility: "private",
      group_ids: [],
      owner: ME,
      version: 1,
      changed_at: "2026-10-05T09:12:00Z",
      copied_from: "builtin.volume_by_queue",
      overview: false,
      read_only: false,
    },
    {
      key: "102",
      kind: "saved",
      id: 102,
      name: "Team-SLA je Kunde",
      description: "SLA-Erfüllung und Verstöße je Kunde für das Support-Team.",
      definition: {
        schema_version: 1,
        source: "tickets",
        dimensions: [d("customer", { choices: [], top_n: 10 })],
        measures: [m("tickets"), m("sla_met_rate"), m("response_breach_rate")],
        parameters: [],
        time_range: { kind: "relative", unit: "day", count: 90, include_current: true },
        visualization: { type: "table", chart_measures: [0] },
        sort: [],
        totals: { grand: true, rows: false, columns: false },
      } as ReportDefinition,
      visibility: "groups",
      group_ids: [2],
      owner: OTHER,
      version: 3,
      changed_at: "2026-10-02T14:40:00Z",
      overview: false,
      read_only: true,
    },
  ] as ReportItem[];
}

export const DEMO_SHARE_GROUPS: ShareGroup[] = [
  { id: 2, name: "users" },
  { id: 3, name: "Support" },
  { id: 4, name: "Technik" },
];

let reports: ReportItem[] = demoSavedReports();
let nextReportId = 103;

function visible(r: ReportItem): boolean {
  return r.visibility !== "private" || r.owner?.user_id === ME.user_id;
}

function findSaved(key: string): ReportItem | undefined {
  return reports.find((r) => r.key === key && visible(r));
}

setSavedReportLookup((key) => findSaved(key)?.definition ?? undefined);

// --- saved searches ----------------------------------------------------------

const cond = (field: string, op: string, value?: unknown) => ({ field, op, value });

function demoSavedSearches(): SavedSearch[] {
  const base = {
    criteria_error: null,
    include_archived: false,
    source: "tiqora" as const,
    source_name: null,
    version: 1,
    changed_at: "2026-10-06T10:00:00Z",
  };
  return [
    {
      ...base,
      id: 11,
      name: "Meine offenen Tickets",
      criteria: { version: 1, where: { all: [cond("owner_id", "eq", 1), cond("state_type", "in", ["new", "open"])] } },
      group_ids: [],
      owner: ME,
      is_owner: true,
      can_edit: true,
      position: 1,
      show_in_sidebar: true,
      show_count: true,
      sort_by: "changed",
      sort_order: "desc",
    },
    {
      ...base,
      id: 12,
      name: "Eskalierte Tickets (Team)",
      criteria: { version: 1, where: { all: [cond("escalated", "is_true"), cond("state_type", "in", ["new", "open"])] } },
      group_ids: [3],
      owner: OTHER,
      is_owner: false,
      can_edit: false,
      position: 2,
      show_in_sidebar: true,
      show_count: true,
      sort_by: "deadline",
      sort_order: "asc",
    },
    {
      ...base,
      id: 13,
      name: "Unbearbeitet seit 3 Tagen",
      criteria: { version: 1, where: { all: [cond("unassigned", "is_true"), cond("created", "older_than", { amount: 3, unit: "day" })] } },
      group_ids: [],
      owner: ME,
      is_owner: true,
      can_edit: true,
      position: 3,
      show_in_sidebar: false,
      show_count: false,
      sort_by: "age",
      sort_order: "desc",
    },
  ] as SavedSearch[];
}

let searches: SavedSearch[] = demoSavedSearches();
let nextSearchId = 14;
/** Counts the demo sidebar shows; a search created in the demo gets a stable made-up count. */
const COUNTS: Record<number, number> = { 11: 9, 12: 4, 13: 2 };

function searchVisible(s: SavedSearch): boolean {
  return s.is_owner || s.group_ids.length > 0;
}

/** A Znuny preview: one profile that maps completely and one with a full-text item. */
export function demoZnunyPreview(): ZnunyImportPreview {
  return {
    profiles: [
      {
        name: "Offen in Support",
        criteria: { version: 1, where: { all: [cond("queue_id", "in", [1]), cond("state_type", "in", ["new", "open"])] } },
        include_archived: false,
        existing_id: null,
        importable: true,
        partial: false,
        unmapped: [],
      },
      {
        name: "Gemischt",
        criteria: { version: 1, where: { all: [cond("title", "contains", "Drucker")] } },
        include_archived: false,
        existing_id: null,
        importable: true,
        partial: true,
        unmapped: [{ key: "Fulltext", value: "Drucker", reason: "Volltextsuche wird nicht unterstützt." }],
      },
    ],
  } as ZnunyImportPreview;
}

/** The ticket fields the demo filter builder offers (`GET /ticket-filters/fields`). */
export function demoTicketFilterFields(): FilterFieldOut[] {
  const f = (key: string, type: FilterFieldOut["type"], ops: string[], options_source: string | null = null, enum_values: string[] = []) => ({
    key,
    label: null,
    label_key: `ticketFilter.fields.${key}`,
    type,
    ops,
    options_source,
    enum_values,
  });
  const idOps = ["eq", "in", "neq", "not_in"];
  const strOps = ["contains", "eq", "is_empty", "is_not_empty", "neq", "starts_with"];
  return [
    f("tn", "string", strOps),
    f("title", "string", strOps),
    f("queue_id", "id", idOps, "queue"),
    f("state_id", "id", idOps, "state"),
    f("state_type", "string", ["eq", "in", "neq", "not_in"]),
    f("priority_id", "id", idOps, "priority"),
    f("owner_id", "id", idOps, "user"),
    f("customer_id", "string", strOps),
    f("customer_user_id", "string", strOps),
    f("channel", "enum", ["eq", "in"], null, ["email", "phone", "telegram", "webchat"]),
    f("unassigned", "bool", ["is_false", "is_true"]),
    f("escalated", "bool", ["is_false", "is_true"]),
    f("created", "datetime", ["after", "before", "between", "older_than", "within_last"]),
    f("changed", "datetime", ["after", "before", "between", "older_than", "within_last"]),
  ];
}

// --- files -----------------------------------------------------------------

function yamlScalar(v: unknown): string {
  if (typeof v === "string") return /^[A-Za-z0-9_.-]+$/.test(v) && !/^(true|false|null|[0-9.-]+)$/.test(v) ? v : JSON.stringify(v);
  return String(v);
}
function yamlLines(v: unknown, indent: string): string[] {
  if (Array.isArray(v)) {
    if (v.length === 0) return [`${indent}[]`];
    return v.flatMap((item) => {
      if (item !== null && typeof item === "object") {
        const [first, ...rest] = yamlLines(item, indent + "  ");
        return [`${indent}- ${first.trimStart()}`, ...rest];
      }
      return [`${indent}- ${yamlScalar(item)}`];
    });
  }
  if (v !== null && typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    if (entries.length === 0) return [`${indent}{}`];
    return entries.flatMap(([k, val]) => {
      if (val !== null && typeof val === "object" && (Array.isArray(val) ? val.length > 0 : Object.keys(val).length > 0))
        return [`${indent}${k}:`, ...yamlLines(val, indent + "  ")];
      return [`${indent}${k}: ${val === null ? "null" : Array.isArray(val) ? "[]" : typeof val === "object" ? "{}" : yamlScalar(val)}`];
    });
  }
  return [`${indent}${yamlScalar(v)}`];
}

/** The body of `GET /reports/{key}/file` (the MSW handler adds the headers); undefined for an unknown key. */
export function demoReportFile(key: string, format: "json" | "yaml"): { body: string; filename: string; contentType: string } | undefined {
  const saved = findSaved(key);
  const builtin = DEMO_REPORTS.find((r) => r.key === key);
  const name = saved?.name ?? (builtin ? builtin.title_key.replace(/^reports\.builtin\./, "").replace(/\.title$/, "") : undefined);
  const definition = saved?.definition ?? builtin?.definition;
  if (!name || !definition) return undefined;
  const file = { format: "tiqora-report", format_version: 1, name, description: saved?.description ?? "", definition };
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "bericht";
  return format === "yaml"
    ? { body: yamlLines(file, "").join("\n") + "\n", filename: `${slug}.yaml`, contentType: "application/yaml" }
    : { body: JSON.stringify(file, null, 2) + "\n", filename: `${slug}.json`, contentType: "application/json" };
}

function importReportFile(content: string): ReportFile {
  try {
    const parsed = JSON.parse(content) as Partial<ReportFile>;
    if (parsed && parsed.definition) {
      return {
        format: "tiqora-report",
        format_version: 1,
        name: parsed.name ?? "Importierter Bericht",
        description: parsed.description ?? "",
        definition: parsed.definition,
      };
    }
  } catch {
    // not JSON (YAML cannot be parsed in the demo): fall through to the sample file
  }
  return {
    format: "tiqora-report",
    format_version: 1,
    name: "Importierter Bericht",
    description: "",
    definition: builtinDefinition("builtin.volume_by_queue"),
  };
}

// --- request resolver --------------------------------------------------------

function pageOf(source: { items: unknown[] } | undefined, body: { offset?: number; limit?: number } | undefined) {
  const items = source?.items ?? [];
  const offset = body?.offset ?? 0;
  const limit = body?.limit ?? 50;
  return { items: items.slice(offset, offset + limit), total: items.length, offset, limit };
}

function bumpReport(r: ReportItem, body: SavedReportIn | SavedReportUpdateIn): ReportItem {
  const visibility = body.visibility;
  return {
    ...r,
    name: body.name,
    description: body.description,
    definition: body.definition,
    visibility,
    group_ids: visibility === "groups" ? (body.group_ids ?? []) : [],
    copied_from: body.copied_from ?? r.copied_from ?? null,
    changed_at: NOW,
  };
}

function ownSearchFields(body: SavedSearchIn | SavedSearchUpdateIn): Partial<SavedSearch> {
  const lp = body.list_params;
  return {
    name: body.name,
    criteria: (body.criteria ?? (lp ? { version: 1 } : null)) as TicketFilter | null,
    group_ids: body.group_ids ?? [],
    include_archived: body.include_archived,
    show_in_sidebar: body.show_in_sidebar,
    show_count: body.show_count,
    sort_by: body.sort_by,
    sort_order: body.sort_order,
    changed_at: NOW,
  };
}

/**
 * Pure stand-in for the saved-report, saved-search and ticket-query routes.
 * Returns the response body, `null` for an empty 204 answer, or `undefined`
 * when the route is not one of these (the caller falls back to its default).
 * `ticketPage` is the demo ticket list `POST /tickets/query` pages through.
 */
export function resolveSavedRequest(
  method: string,
  path: string,
  body: unknown,
  ticketPage?: { items: unknown[] },
): unknown | undefined {
  const at = /\/api\/v1(\/[^?#]*)$/.exec(path);
  if (!at) return undefined;
  const rest = at[1].replace(/\/+$/, "");
  const verb = method.toUpperCase();
  let hit: RegExpExecArray | null;

  // --- reports
  if (rest === "/reports") {
    if (verb === "GET") {
      return { reports: [...DEMO_REPORTS, ...reports.filter(visible)], facts: demoFacts() };
    }
    if (verb === "POST") {
      const id = nextReportId++;
      const input = body as SavedReportIn;
      const made = bumpReport(
        { key: String(id), kind: "saved", id, owner: ME, version: 1, overview: false, read_only: false } as ReportItem,
        input,
      );
      reports = [...reports, made];
      return made;
    }
  }
  if (rest === "/reports/import-file" && verb === "POST") return importReportFile((body as { content?: string } | undefined)?.content ?? "");
  if (rest === "/reference/share-groups" && verb === "GET") return DEMO_SHARE_GROUPS;
  if ((hit = /^\/reports\/([^/]+)$/.exec(rest))) {
    const key = decodeURIComponent(hit[1]);
    if (verb === "GET") {
      const builtin = DEMO_REPORTS.find((r) => r.key === key);
      return builtin ?? findSaved(key);
    }
    const current = reports.find((r) => r.key === key && visible(r));
    if (!current) return undefined;
    if (verb === "PUT") {
      const input = body as SavedReportUpdateIn;
      const next = { ...bumpReport(current, input), version: (current.version ?? 1) + 1 };
      reports = reports.map((r) => (r.key === key ? next : r));
      return next;
    }
    if (verb === "DELETE") {
      reports = reports.filter((r) => r.key !== key);
      return null;
    }
  }

  // --- saved searches
  if (rest === "/saved-searches") {
    if (verb === "GET") return searches.filter(searchVisible);
    if (verb === "POST") {
      const id = nextSearchId++;
      const made = {
        ...demoSavedSearches()[0],
        ...ownSearchFields(body as SavedSearchIn),
        id,
        owner: ME,
        is_owner: true,
        can_edit: true,
        position: searches.length + 1,
        version: 1,
        source: "tiqora",
        source_name: null,
        criteria_error: null,
      } as SavedSearch;
      searches = [...searches, made];
      return made;
    }
  }
  if (rest === "/saved-searches/counts" && verb === "GET") {
    const counts: Record<string, number | null> = {};
    for (const s of searches.filter(searchVisible)) if (s.show_in_sidebar && s.show_count) counts[String(s.id)] = COUNTS[s.id] ?? 5;
    return { counts };
  }
  if (rest === "/saved-searches/import/znuny") {
    if (verb === "GET") {
      return {
        profiles: demoZnunyPreview().profiles.map((p) => ({
          ...p,
          existing_id: searches.find((s) => s.source === "znuny" && s.source_name === p.name)?.id ?? null,
        })),
      };
    }
    if (verb === "POST") {
      const names = (body as { names?: string[] } | undefined)?.names ?? [];
      const out = { created: [] as number[], updated: [] as number[], skipped: [] as { name: string; reason: string }[] };
      for (const name of names) {
        const profile = demoZnunyPreview().profiles.find((p) => p.name === name);
        if (!profile) {
          out.skipped.push({ name, reason: "Unbekanntes Profil." });
          continue;
        }
        const existing = searches.find((s) => s.source === "znuny" && s.source_name === name);
        if (existing) {
          searches = searches.map((s) => (s === existing ? { ...s, criteria: profile.criteria, version: s.version + 1, changed_at: NOW } : s));
          out.updated.push(existing.id);
          continue;
        }
        const id = nextSearchId++;
        searches = [
          ...searches,
          {
            ...demoSavedSearches()[0],
            id,
            name,
            criteria: profile.criteria,
            group_ids: [],
            owner: ME,
            is_owner: true,
            can_edit: true,
            position: searches.length + 1,
            show_in_sidebar: false,
            show_count: false,
            source: "znuny",
            source_name: name,
            version: 1,
          } as SavedSearch,
        ];
        out.created.push(id);
      }
      return out;
    }
  }
  if ((hit = /^\/saved-searches\/(\d+)\/prefs$/.exec(rest)) && verb === "PUT") {
    const id = Number(hit[1]);
    const current = searches.find((s) => s.id === id && searchVisible(s));
    if (!current) return undefined;
    const prefs = body as SavedSearchPrefsIn;
    const next = { ...current, show_in_sidebar: prefs.show_in_sidebar, show_count: prefs.show_count, position: prefs.position ?? current.position };
    searches = searches.map((s) => (s.id === id ? next : s));
    return next;
  }
  if ((hit = /^\/saved-searches\/(\d+)$/.exec(rest))) {
    const id = Number(hit[1]);
    const current = searches.find((s) => s.id === id && searchVisible(s));
    if (!current) return undefined;
    if (verb === "GET") return current;
    if (verb === "PUT") {
      const next = { ...current, ...ownSearchFields(body as SavedSearchUpdateIn), version: current.version + 1 } as SavedSearch;
      searches = searches.map((s) => (s.id === id ? next : s));
      return next;
    }
    if (verb === "DELETE") {
      searches = searches.filter((s) => s.id !== id);
      return null;
    }
  }

  // --- ticket filters and the structured ticket query
  if (rest === "/ticket-filters/fields" && verb === "GET") return demoTicketFilterFields();
  if (rest === "/ticket-filters/count" && verb === "POST") return { count: ticketPage?.items.length ?? 0 };
  if (rest === "/tickets/query" && verb === "POST") return pageOf(ticketPage, body as { offset?: number; limit?: number } | undefined);
  return undefined;
}

export { demoSavedReports, demoSavedSearches };
