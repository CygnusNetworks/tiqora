/**
 * Demo / e2e mock of the CMDB API (`/api/v1/cmdb/*`, `/api/v1/admin/cmdb/*`, `/api/portal/config-items*`).
 * Pure and framework-free like `dashboardsMock.ts`: `resolveCmdb(store, method, path, body, query)` answers
 * with `{status, body}` or `undefined` for a route it does not know. Two classes (Server, Standort), six
 * CIs, one `DependsOn` chain with a CI in incident (so the badges and the graph show something) and one
 * link to a CI the caller may not see. Names use example.org only.
 */
import type {
  Affected,
  AttributeDef,
  CatalogItem,
  Ci,
  CiHistoryPage,
  CiListItem,
  CiPage,
  CiTicket,
  CiVersion,
  CiVersionSummary,
  CmdbAttachment,
  CmdbClass,
  CmdbDefinition,
  CmdbSavedSearch,
  CmdbStatus,
  FilterField,
  FormOptions,
  ImpactGraph,
  ImportTemplate,
  ImportTemplateIn,
  LinkOut,
  LinkType,
  RenderedAttr,
} from "@/lib/cmdbApi";
import type { CatalogItemIn, ClassTemplate, CmdbAdminStatus, CmdbClassIn, CmdbClassUpdateIn } from "@/lib/cmdbAdminApi";
import type { PortalCiDetail, PortalCiPage } from "@/lib/portalCmdbApi";
import { demoItsmMode } from "./itsmMode";

export type DemoCmdbResponse = { status: number; body: unknown };

type AttrEntry = { v: string | number | null };
type DemoCi = {
  id: number;
  number: string;
  classId: number;
  name: string;
  deployment: number;
  incidentOwn: number;
  incidentCurrent: number;
  attrs: Record<string, AttrEntry[]>;
  lockVersion: number;
};
/** CI-to-CI link; a link to a ticket, service, customer or article keeps the CI as `source` and the other end in `ext`. */
type DemoLink = { id: number; type: string; source: number; target: number; ext?: { kind: LinkOut["other"]["kind"]; key: string; label: string; url: string | null } };

export const DEMO_CATALOG_DEPLOYMENT = "ITSM::ConfigItem::DeploymentState";
export const DEMO_CATALOG_INCIDENT = "ITSM::Core::IncidentState";
const CATALOG_OS = "CMDB::OS";
const AT = "2026-10-08T09:30:00";
const HIDDEN_CI_ID = 99;

const CATALOG: CatalogItem[] = [
  { id: 1, catalog_class: DEMO_CATALOG_DEPLOYMENT, item_key: "planned", name: "Geplant", functionality: "preproductive", position: 1, valid: true },
  { id: 2, catalog_class: DEMO_CATALOG_DEPLOYMENT, item_key: "production", name: "Produktiv", functionality: "productive", position: 2, valid: true },
  { id: 3, catalog_class: DEMO_CATALOG_DEPLOYMENT, item_key: "retired", name: "Außer Betrieb", functionality: "postproductive", position: 3, valid: true },
  { id: 11, catalog_class: DEMO_CATALOG_INCIDENT, item_key: "operational", name: "Betriebsbereit", functionality: "operational", position: 1, valid: true },
  { id: 12, catalog_class: DEMO_CATALOG_INCIDENT, item_key: "warning", name: "Warnung", functionality: "warning", position: 2, valid: true },
  { id: 13, catalog_class: DEMO_CATALOG_INCIDENT, item_key: "incident", name: "Vorfall", functionality: "incident", position: 3, valid: true },
  { id: 21, catalog_class: CATALOG_OS, item_key: "debian", name: "Debian", functionality: null, position: 1, valid: true },
  { id: 22, catalog_class: CATALOG_OS, item_key: "ubuntu", name: "Ubuntu", functionality: null, position: 2, valid: true },
];
const catalogItem = (id: number): CatalogItem => CATALOG.find((c) => c.id === id) ?? CATALOG[0];

type AttrInit = Omit<Partial<AttributeDef>, "options"> & Pick<AttributeDef, "key" | "name" | "type"> & { options?: Partial<NonNullable<AttributeDef["options"]>> };
/** Fills the defaults the API always sends, so the sketches below stay short. */
const attr = (a: AttrInit): AttributeDef => ({
  count_min: 0,
  count_max: 1,
  count_default: 1,
  required: false,
  searchable: false,
  portal: false,
  ...a,
  options: { multiline: false, ...a.options },
});

const SERVER_DEF: CmdbDefinition = {
  id: 1,
  class_id: 1,
  version: 1,
  comment: "Ausgangsversion",
  created_at: AT,
  created_by: 1,
  attributes: [
    attr({ key: "hostname", name: "Hostname", type: "text", required: true, searchable: true, portal: true }),
    attr({ key: "ip", name: "IP-Adresse", type: "ip", count_min: 0, count_max: 3, count_default: 1, portal: true }),
    attr({ key: "os", name: "Betriebssystem", type: "catalog", options: { catalog_class: CATALOG_OS } }),
    attr({ key: "location", name: "Standort", type: "ci_ref", options: { class_keys: ["site"] } }),
  ],
};
const SITE_DEF: CmdbDefinition = {
  id: 2,
  class_id: 2,
  version: 1,
  comment: "Ausgangsversion",
  created_at: AT,
  created_by: 1,
  attributes: [
    attr({ key: "address", name: "Adresse", type: "text", searchable: true, portal: true }),
    attr({ key: "contact", name: "Ansprechperson", type: "text" }),
  ],
};

const CLASSES: CmdbClass[] = [
  { id: 1, class_key: "server", name: "Server", icon: "server", number_prefix: "SRV", group_id: 1, group_name: "users", portal_visible: true, valid: true, position: 1, lock_version: 1, can_write: true, current_definition_version: 1, definition: SERVER_DEF },
  { id: 2, class_key: "site", name: "Standort", icon: "building", number_prefix: "LOC", group_id: 1, group_name: "users", portal_visible: false, valid: true, position: 2, lock_version: 1, can_write: true, current_definition_version: 1, definition: SITE_DEF },
];
const DEMO_EXT_LABELS: Record<string, Record<string, string>> = {
  ticket: { "1": "2026100801 – Datenbank antwortet nicht", "2": "2026100802 – Backup-Fenster überschritten" },
  service: { "1": "E-Mail", "2": "VPN", "3": "Arbeitsplatz" },
  kb_article: { "700": "How to reset your password", "701": "Setting up the VPN client" },
};

const classOf = (id: number): CmdbClass => CLASSES.find((c) => c.id === id) ?? CLASSES[0];

const LINK_TYPES: LinkType[] = [
  { name: "DependsOn", pointed: true, propagation: "source", source_label: "hängt ab von", target_label: "wird benötigt von", position: 1, offer_ci_ci: true, offer_ci_service: true, offer_ticket_ci: false },
  { name: "ConnectedTo", pointed: false, propagation: "none", source_label: "verbunden mit", target_label: "verbunden mit", position: 2, offer_ci_ci: true, offer_ci_service: false, offer_ticket_ci: false },
  { name: "RelevantTo", pointed: true, propagation: "none", source_label: "betrifft", target_label: "betroffen von", position: 3, offer_ci_ci: false, offer_ci_service: false, offer_ticket_ci: true },
];

function seedCis(): DemoCi[] {
  const host = (id: number, name: string, own: number, cur: number, ip: string, os: number): DemoCi => ({
    id,
    number: `SRV-${String(id).padStart(4, "0")}`,
    classId: 1,
    name,
    deployment: 2,
    incidentOwn: own,
    incidentCurrent: cur,
    lockVersion: 2,
    attrs: { hostname: [{ v: name }], ip: [{ v: ip }], os: [{ v: os }], location: [{ v: 5 }] },
  });
  const site = (id: number, name: string, address: string): DemoCi => ({
    id,
    number: `LOC-${String(id).padStart(4, "0")}`,
    classId: 2,
    name,
    deployment: 2,
    incidentOwn: 11,
    incidentCurrent: 11,
    lockVersion: 1,
    attrs: { address: [{ v: address }], contact: [{ v: "Erika Beispiel" }] },
  });
  return [
    host(1, "web-01.example.org", 11, 12, "192.0.2.11", 21),
    host(2, "web-02.example.org", 11, 12, "192.0.2.12", 21),
    host(3, "db-01.example.org", 13, 13, "192.0.2.21", 22),
    host(4, "lb-01.example.org", 11, 11, "192.0.2.5", 22),
    site(5, "Rechenzentrum Nord", "Musterweg 1, 12345 Beispielstadt"),
    site(6, "Büro Bonn", "Beispielallee 2, 53111 Bonn"),
  ];
}

type DemoJob = {
  id: number;
  kind: string;
  status: "succeeded" | "failed";
  title_key: string;
  progress_done: number;
  progress_total: number;
  progress_message: null;
  cancel_requested: boolean;
  error: string | null;
  result: Record<string, unknown> | null;
  files: Array<{ id: number; name: string; content_type: string; size_bytes: number }>;
  created_by: number;
  created_at: string;
  started_at: string;
  finished_at: string;
};

export type DemoCmdb = {
  cis: DemoCi[];
  links: DemoLink[];
  nextCiId: number;
  nextLinkId: number;
  nextAttachmentId: number;
  saved: CmdbSavedSearch[];
  nextSavedId: number;
  jobs: Record<number, DemoJob>;
  nextJobId: number;
  /** Text of the report files, by job id (served by the jobs file route of the demo / e2e mock). */
  reports: Record<number, string>;
  templates: ImportTemplate[];
  nextTemplateId: number;
};

/** Finished background job of the demo (bulk edit / export): answers `GET /api/v1/jobs/:id`. */
/** Text of a job's report file, or undefined (the file route answers 404). */
export function demoCmdbReport(store: DemoCmdb, jobId: number): string | undefined {
  return store.reports[jobId];
}

export function demoCmdbJob(store: DemoCmdb, id: number): DemoJob | undefined {
  return store.jobs[id];
}

function finishedJob(
  store: DemoCmdb,
  kind: "cmdb_bulk" | "cmdb_export" | "cmdb_import",
  result: Record<string, unknown> | null,
  files: DemoJob["files"],
  failure: string | null = null,
): number {
  const id = store.nextJobId++;
  store.jobs[id] = {
    id,
    kind: `core.${kind}`,
    status: failure ? "failed" : "succeeded",
    title_key: `jobs.kinds.core.${kind}`,
    progress_done: 1,
    progress_total: 1,
    progress_message: null,
    cancel_requested: false,
    error: failure,
    result,
    files,
    created_by: 1,
    created_at: AT,
    started_at: AT,
    finished_at: AT,
  };
  return id;
}

export function createDemoCmdb(): DemoCmdb {
  return {
    cis: seedCis(),
    links: [
      { id: 1, type: "DependsOn", source: 1, target: 3 },
      { id: 2, type: "DependsOn", source: 2, target: 3 },
      { id: 3, type: "DependsOn", source: 1, target: 4 },
      { id: 4, type: "DependsOn", source: 3, target: HIDDEN_CI_ID }, // the one the caller may not see
    ],
    nextCiId: 7,
    nextLinkId: 5,
    nextAttachmentId: 1,
    saved: [],
    nextSavedId: 1,
    jobs: {},
    nextJobId: 9311,
    reports: {},
    templates: [],
    nextTemplateId: 1,
  };
}

/** Shared store of the running demo; e2e fixtures create their own. */
export const demoCmdb: DemoCmdb = createDemoCmdb();

function renderAttrs(store: DemoCmdb, ci: DemoCi): RenderedAttr[] {
  const def = classOf(ci.classId).definition?.attributes ?? [];
  return def.map((a) => {
    const raw = ci.attrs[a.key]?.[0]?.v ?? null;
    const display =
      a.type === "catalog" && typeof raw === "number"
        ? catalogItem(raw).name
        : a.type === "ci_ref" && typeof raw === "number"
          ? (store.cis.find((c) => c.id === raw)?.name ?? String(raw))
          : raw === null
            ? null
            : String(raw);
    return {
      key: a.key,
      label: a.name,
      path: a.key,
      type: a.type,
      portal: a.portal ?? false,
      display,
      children: [],
      ref: a.type === "ci_ref" && typeof raw === "number" ? { kind: "ci", key: String(raw) } : null,
    };
  });
}

function listItem(ci: DemoCi): CiListItem {
  const dep = catalogItem(ci.deployment);
  const inc = catalogItem(ci.incidentCurrent);
  return {
    id: ci.id,
    number: ci.number,
    name: ci.name,
    class_key: classOf(ci.classId).class_key,
    class_name: classOf(ci.classId).name,
    deployment_state: dep.name,
    deployment_functionality: dep.functionality,
    incident_state: inc.name,
    incident_functionality: inc.functionality,
    changed_at: AT,
  };
}

function versionOut(store: DemoCmdb, ci: DemoCi, id: number): CiVersion {
  return {
    id,
    ci_id: ci.id,
    definition_id: classOf(ci.classId).definition?.id ?? 1,
    definition_version: 1,
    name: ci.name,
    deployment_state_id: ci.deployment,
    incident_state_id: ci.incidentOwn,
    attributes: ci.attrs,
    rendered: renderAttrs(store, ci),
    change_note: null,
    created_by: 1,
    created_at: AT,
    archived: false,
  };
}
const versionIds = (ci: DemoCi): number[] => (ci.lockVersion > 1 ? [ci.id * 10 + 1, ci.id * 10 + 2] : [ci.id * 10 + 1]);

function ciOut(store: DemoCmdb, ci: DemoCi): Ci {
  const ids = versionIds(ci);
  const cls = classOf(ci.classId);
  return {
    id: ci.id,
    number: ci.number,
    name: ci.name,
    class: { id: cls.id, class_key: cls.class_key, name: cls.name, icon: cls.icon, group_id: cls.group_id },
    deployment_state_id: ci.deployment,
    incident_state_id: ci.incidentOwn,
    states: { deployment: catalogItem(ci.deployment), incident_own: catalogItem(ci.incidentOwn), incident_current: catalogItem(ci.incidentCurrent) },
    version_count: ids.length,
    lock_version: ci.lockVersion,
    current_version: versionOut(store, ci, ids[ids.length - 1]),
    rendered: renderAttrs(store, ci),
    unknown_keys: [],
    can_write: true,
    created_at: AT,
    created_by: 1,
    changed_at: AT,
    changed_by: 1,
  };
}

function linkOut(store: DemoCmdb, link: DemoLink, ciId: number): LinkOut {
  if (link.ext) {
    const type = LINK_TYPES.find((t) => t.name === link.type) ?? LINK_TYPES[0];
    return {
      id: link.id,
      link_type: link.type,
      link_type_label: type.source_label ?? link.type,
      direction: type.pointed ? "outgoing" : "undirected",
      created_at: AT,
      created_by: "agent",
      other: { ...link.ext, hidden: false },
    };
  }
  const outgoing = link.source === ciId;
  const otherId = outgoing ? link.target : link.source;
  const other = store.cis.find((c) => c.id === otherId);
  const type = LINK_TYPES.find((t) => t.name === link.type) ?? LINK_TYPES[0];
  const label = (outgoing ? type.source_label : type.target_label) ?? link.type;
  return {
    id: link.id,
    link_type: link.type,
    link_type_label: label,
    direction: type.pointed ? (outgoing ? "outgoing" : "incoming") : "undirected",
    created_at: AT,
    created_by: "agent",
    other: other
      ? { kind: "ci", key: String(other.id), label: `${other.number} – ${other.name}`, hidden: false, url: `/agent/cmdb/${other.id}` }
      : { kind: "ci", key: String(otherId), label: "Kein Zugriff", hidden: true, url: null },
  };
}

function impact(store: DemoCmdb, rootId: number): ImpactGraph {
  const seen = new Map<number, number>([[rootId, 0]]);
  const edges: ImpactGraph["edges"] = [];
  const queue = [rootId];
  while (queue.length) {
    const cur = queue.shift() as number;
    for (const l of store.links.filter((x) => !x.ext && (x.source === cur || x.target === cur))) {
      const other = l.source === cur ? l.target : l.source;
      edges.push({ source: `ci:${l.source}`, target: `ci:${l.target}`, link_type: l.type, propagates: l.type === "DependsOn" });
      if (!seen.has(other)) {
        seen.set(other, (seen.get(cur) ?? 0) + 1);
        queue.push(other);
      }
    }
  }
  const uniq = edges.filter((e, i) => edges.findIndex((x) => x.source === e.source && x.target === e.target && x.link_type === e.link_type) === i);
  return {
    root: `ci:${rootId}`,
    truncated: false,
    edges: uniq,
    nodes: [...seen.entries()].map(([id, depth]) => {
      const ci = store.cis.find((c) => c.id === id);
      return ci
        ? { id: `ci:${id}`, kind: "ci" as const, ci_id: id, number: ci.number, name: ci.name, class_key: classOf(ci.classId).class_key, incident_functionality: catalogItem(ci.incidentCurrent).functionality, depth, hidden: false }
        : { id: `ci:${id}`, kind: "ci" as const, ci_id: null, depth, hidden: true };
    }),
  };
}

const matches = (ci: DemoCi, q: string): boolean => {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  const hay = [ci.number, ci.name, ...Object.values(ci.attrs).flatMap((es) => es.map((e) => String(e.v ?? "")))];
  return needle.split(/\s+/).every((tok) => hay.some((h) => h.toLowerCase().includes(tok)));
};

const SORTS: Record<string, (c: DemoCi) => string> = {
  number: (c) => c.number,
  name: (c) => c.name.toLowerCase(),
  class: (c) => classOf(c.classId).name,
  changed_at: () => AT,
  deployment_state: (c) => catalogItem(c.deployment).name,
  incident_state: (c) => catalogItem(c.incidentCurrent).name,
};

type Cond = { field: string; op: string; value?: unknown };
type SearchBody = {
  class_ids?: number[];
  q?: string | null;
  sort?: string;
  order?: string;
  offset?: number;
  limit?: number;
  filter?: { where?: { all?: Cond[] } | null } | null;
};

/** The conditions the demo list can build: states and `attr.<key>` text conditions. */
function matchesCond(ci: DemoCi, c: Cond): boolean {
  const list = Array.isArray(c.value) ? c.value.map(String) : [];
  if (c.field === "deployment_state") return list.includes(String(ci.deployment));
  if (c.field === "incident_functionality") return list.includes(String(catalogItem(ci.incidentCurrent).functionality));
  if (c.field.startsWith("attr.")) {
    const vals = (ci.attrs[c.field.slice(5)] ?? []).map((e) => String(e.v ?? "").toLowerCase());
    const needle = String(c.value ?? "").toLowerCase();
    switch (c.op) {
      case "eq":
        return vals.includes(needle);
      case "neq":
        return !vals.includes(needle);
      case "contains":
        return vals.some((v) => v.includes(needle));
      case "starts_with":
        return vals.some((v) => v.startsWith(needle));
      case "is_empty":
        return vals.every((v) => v === "");
      case "is_not_empty":
        return vals.some((v) => v !== "");
      default:
        return true;
    }
  }
  return true;
}

function search(store: DemoCmdb, body: SearchBody): CiPage {
  const conds = body.filter?.where?.all ?? [];
  let rows = store.cis.filter(
    (c) =>
      (body.class_ids?.length ? body.class_ids.includes(c.classId) : true) && matches(c, body.q ?? "") && conds.every((cond) => matchesCond(c, cond)),
  );
  const key = SORTS[body.sort ?? "number"] ?? SORTS.number;
  rows = [...rows].sort((a, b) => key(a).localeCompare(key(b)) * (body.order === "desc" ? -1 : 1));
  const offset = body.offset ?? 0;
  return { items: rows.slice(offset, offset + (body.limit ?? 50)).map(listItem), total: rows.length, total_capped: false };
}

const FILTER_FIELDS: FilterField[] = [
  { key: "number", label: "Nummer", label_key: null, type: "string", ops: ["eq", "starts_with"], enum_values: [], options_source: null },
  { key: "name", label: "Name", label_key: null, type: "string", ops: ["contains", "eq"], enum_values: [], options_source: null },
  { key: "incident_state", label: "Vorfallsstatus", label_key: null, type: "enum", ops: ["eq", "in"], enum_values: ["operational", "warning", "incident"], options_source: null },
];

/** Base fields plus the searchable attributes of one class (text attributes only in the demo). */
function filterFields(classId: number | undefined): FilterField[] {
  if (classId === undefined) return FILTER_FIELDS;
  const attrs = (classOf(classId).definition?.attributes ?? []).filter((a) => a.searchable || a.type === "ip");
  return [
    ...FILTER_FIELDS,
    ...attrs.map<FilterField>((a) => ({
      key: `attr.${a.key}`,
      label: a.name,
      label_key: null,
      type: "string",
      ops: ["eq", "neq", "contains", "starts_with", "is_empty", "is_not_empty"],
      enum_values: [],
      options_source: null,
    })),
  ];
}

const FORM_OPTIONS: FormOptions = {
  catalog: { [CATALOG_OS]: CATALOG.filter((c) => c.catalog_class === CATALOG_OS).map((c) => ({ value: String(c.id), label: c.name })) },
};

const TICKETS: CiTicket[] = [
  { ticket_id: 1, number: "2026100801", title: "Datenbank antwortet nicht", state_name: "open", state_type: "open", queue_id: 1, queue_name: "Raw", link_type: "RelevantTo", open: true },
];

const CLOSED_TICKET: CiTicket = { ticket_id: 2, number: "2026092201", title: "Festplatte getauscht", state_name: "closed successful", state_type: "closed", queue_id: 1, queue_name: "Raw", link_type: "RelevantTo", open: false };

const CLASS_TEMPLATES: ClassTemplate[] = [
  { key: "computer", name: "Computer", icon: "computer", attribute_count: 15 },
  { key: "hardware", name: "Hardware", icon: "hardware", attribute_count: 10 },
  { key: "location", name: "Standort", icon: "location", attribute_count: 4 },
  { key: "network", name: "Netzwerk", icon: "network", attribute_count: 5 },
  { key: "software", name: "Software", icon: "software", attribute_count: 6 },
];

const adminStatus = (store: DemoCmdb): CmdbAdminStatus => ({
  mode: demoItsmMode(),
  views_present: true,
  catalog_seeded: true,
  class_count: CLASSES.length,
  ci_count: store.cis.length,
  archived_version_count: 0,
});


/** Definition history of the demo classes (version 1 is the seeded definition); new versions append. */
const DEFINITION_HISTORY: Record<number, CmdbDefinition[]> = {};
function definitionsOf(classId: number): CmdbDefinition[] {
  const cls = classOf(classId);
  const hist = (DEFINITION_HISTORY[classId] ??= cls.definition ? [cls.definition] : []);
  return hist;
}

type DemoIssue = { path: string; code: string; message: string };
type DemoChange = { path: string; kind: "added" | "removed" | "type_changed" | "count_changed" | "renamed_label" | "flags_changed"; detail: string };

function flattenAttrs(defs: readonly AttributeDef[], prefix = ""): Array<[string, AttributeDef]> {
  return defs.flatMap((a) => [[`${prefix}${a.key}`, a] as [string, AttributeDef], ...flattenAttrs(a.sub ?? [], `${prefix}${a.key}.`)]);
}

/** Small stand-in of the server validation: key pattern, duplicates, group without sub, unknown catalog class. */
function demoValidate(defs: readonly AttributeDef[]): DemoIssue[] {
  const issues: DemoIssue[] = [];
  const walk = (list: readonly AttributeDef[], prefix: string) => {
    const seen = new Set<string>();
    for (const a of list) {
      const path = `${prefix}${a.key}`;
      if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(a.key)) issues.push({ path, code: "bad_key", message: "Ungültiger Schlüssel." });
      else if (seen.has(a.key.toLowerCase())) issues.push({ path, code: "duplicate_key", message: "Der Schlüssel kommt mehrfach vor." });
      seen.add(a.key.toLowerCase());
      if (a.type === "catalog" && !a.options?.catalog_class) issues.push({ path, code: "catalog_class_missing", message: "Eine Katalogklasse ist erforderlich." });
      if (a.type === "group" && (a.sub ?? []).length === 0) issues.push({ path, code: "group_empty", message: "Eine Gruppe braucht Unterattribute." });
      walk(a.sub ?? [], `${path}.`);
    }
  };
  walk(defs, "");
  return issues;
}

function demoDiff(oldDefs: readonly AttributeDef[], newDefs: readonly AttributeDef[]): DemoChange[] {
  const before = new Map(flattenAttrs(oldDefs));
  const after = new Map(flattenAttrs(newDefs));
  const out: DemoChange[] = [];
  for (const [path, a] of after) {
    const b = before.get(path);
    if (!b) out.push({ path, kind: "added", detail: a.type });
    else {
      if (b.type !== a.type) out.push({ path, kind: "type_changed", detail: `${b.type} -> ${a.type}` });
      if (b.name !== a.name) out.push({ path, kind: "renamed_label", detail: `${b.name} -> ${a.name}` });
      if (b.count_min !== a.count_min || b.count_max !== a.count_max) out.push({ path, kind: "count_changed", detail: `${b.count_min}..${b.count_max} -> ${a.count_min}..${a.count_max}` });
      if (b.required !== a.required || b.searchable !== a.searchable || b.portal !== a.portal) out.push({ path, kind: "flags_changed", detail: "" });
    }
  }
  for (const [path, b] of before) if (!after.has(path)) out.push({ path, kind: "removed", detail: b.type });
  return out;
}

/** Import preview: Tiqora JSON is parsed for real, the Znuny formats answer a fixed two-attribute sample with a warning. */
function demoImport(format: string, text: string): DemoCmdbResponse {
  if (format === "tiqora_json") {
    try {
      const parsed = JSON.parse(text) as { attributes?: AttributeDef[] } | AttributeDef[];
      const list = Array.isArray(parsed) ? parsed : (parsed.attributes ?? []);
      return { status: 200, body: { attributes: list.map((a) => attr(a as AttrInit)), warnings: [], missing_catalog_classes: [] } };
    } catch {
      return { status: 422, body: { detail: "Kein gültiges JSON." } };
    }
  }
  return {
    status: 200,
    body: {
      attributes: [
        attr({ key: "Name", name: "Name", type: "text", required: true, searchable: true }),
        attr({ key: "Type", name: "Typ", type: "catalog", options: { catalog_class: "ITSM::Demo::Type" } }),
      ],
      warnings: ["Die Eingabe wurde als Beispiel gelesen (Demo)."],
      missing_catalog_classes: ["ITSM::Demo::Type"],
    },
  };
}

export function resolveCmdb(
  store: DemoCmdb,
  method: string,
  pathname: string,
  body?: unknown,
  query: URLSearchParams = new URLSearchParams(),
): DemoCmdbResponse | undefined {
  const ok = (b: unknown, status = 200): DemoCmdbResponse => ({ status, body: b });
  const none = (): DemoCmdbResponse => ({ status: 204, body: null });
  const notFound = (): DemoCmdbResponse => ({ status: 404, body: { detail: "Nicht gefunden." } });

  // Customer portal: only portal-visible classes, portal attributes only.
  const portal = /\/api\/portal\/config-items(?:\/(\d+))?$/.exec(pathname);
  if (portal) {
    if (demoItsmMode() !== "tiqora") return notFound();
    const visible = store.cis.filter((c) => classOf(c.classId).portal_visible);
    if (portal[1]) {
      const ci = visible.find((c) => c.id === Number(portal[1]));
      if (!ci) return notFound();
      const out: PortalCiDetail = {
        id: ci.id,
        number: ci.number,
        name: ci.name,
        class_name: classOf(ci.classId).name,
        deployment_state: catalogItem(ci.deployment).name,
        incident_state: catalogItem(ci.incidentCurrent).name,
        incident_functionality: catalogItem(ci.incidentCurrent).functionality,
        rendered: renderAttrs(store, ci).filter((r) => r.portal),
      };
      return ok(out);
    }
    const rows = visible.filter((c) => matches(c, query.get("q") ?? ""));
    const page: PortalCiPage = {
      total: rows.length,
      items: rows.map((ci) => ({
        id: ci.id,
        number: ci.number,
        name: ci.name,
        class_name: classOf(ci.classId).name,
        deployment_state: catalogItem(ci.deployment).name,
        incident_state: catalogItem(ci.incidentCurrent).name,
        incident_functionality: catalogItem(ci.incidentCurrent).functionality,
      })),
    };
    return ok(page);
  }

  const admin = /\/api\/v1\/admin\/cmdb(\/.*)?$/.exec(pathname);
  if (admin) {
    const sub = admin[1] ?? "";
    if (sub === "/status" || sub === "/seed-defaults") return ok(adminStatus(store));
    if (sub === "/classes" && method === "GET") return ok(CLASSES);
    if (sub === "/classes" && method === "POST") {
      const b = body as CmdbClassIn;
      const cls: CmdbClass = {
        id: Math.max(0, ...CLASSES.map((c) => c.id)) + 1,
        class_key: b.class_key,
        name: b.name,
        icon: b.icon ?? null,
        number_prefix: b.number_prefix || `${b.class_key.toUpperCase().slice(0, 15)}-`,
        group_id: b.group_id,
        group_name: "users",
        portal_visible: b.portal_visible ?? false,
        valid: true,
        position: CLASSES.length + 1,
        lock_version: 1,
        can_write: true,
        current_definition_version: null,
        definition: null,
      };
      CLASSES.push(cls);
      return ok(cls, 201);
    }
    const classRoute = /^\/classes\/(\d+)(\/.*)?$/.exec(sub);
    if (classRoute) {
      const cid = Number(classRoute[1]);
      const cls = CLASSES.find((c) => c.id === cid);
      if (!cls) return notFound();
      const rest = classRoute[2] ?? "";
      if (rest === "" && method === "GET") return ok(cls);
      if (rest === "" && method === "PUT") {
        const b = body as CmdbClassUpdateIn;
        if (b.expected_lock_version !== cls.lock_version) return ok({ detail: { code: "conflict", message: "Die Klasse wurde zwischenzeitlich geändert." } }, 409);
        Object.assign(cls, { name: b.name, icon: b.icon ?? null, number_prefix: b.number_prefix ?? cls.number_prefix, group_id: b.group_id, portal_visible: b.portal_visible ?? cls.portal_visible, valid: b.valid ?? cls.valid, lock_version: cls.lock_version + 1 });
        return ok(cls);
      }
      if (rest === "/definitions" && method === "GET") return ok(definitionsOf(cid));
      if (rest === "/definitions" && method === "POST") {
        const b = body as { attributes: AttributeDef[]; comment?: string | null };
        const issues = demoValidate(b.attributes);
        if (issues.length > 0) return ok({ detail: issues[0].message }, 422);
        const hist = definitionsOf(cid);
        const def: CmdbDefinition = { id: 100 + hist.length + cid * 10, class_id: cid, version: (hist[hist.length - 1]?.version ?? 0) + 1, comment: b.comment ?? null, created_at: AT, created_by: 1, attributes: b.attributes.map((a) => attr(a as AttrInit)) };
        hist.push(def);
        cls.definition = def;
        cls.current_definition_version = def.version;
        cls.lock_version += 1;
        return ok(def, 201);
      }
      if (rest === "/definitions/diff") {
        const hist = definitionsOf(cid);
        const from = hist.find((d) => d.version === Number(query.get("from_version")));
        const to = hist.find((d) => d.version === Number(query.get("to_version")));
        return from && to ? ok(demoDiff(from.attributes, to.attributes)) : notFound();
      }
    }
    if (sub === "/definitions/validate" && method === "POST") return ok(demoValidate((body as { attributes: AttributeDef[] }).attributes));
    if (sub === "/definition-import" && method === "POST") {
      const b = body as { format: string; text: string };
      return demoImport(b.format, b.text);
    }
    if (sub === "/class-templates" && method === "GET") return ok(CLASS_TEMPLATES);
    const apply = /^\/class-templates\/([^/]+)\/apply$/.exec(sub);
    if (apply && method === "POST") {
      const tpl = CLASS_TEMPLATES.find((c) => c.key === decodeURIComponent(apply[1]));
      if (!tpl) return notFound();
      const cls: CmdbClass = {
        id: Math.max(0, ...CLASSES.map((c) => c.id)) + 1,
        class_key: tpl.key,
        name: tpl.name,
        icon: tpl.icon,
        number_prefix: `${tpl.key.toUpperCase().slice(0, 15)}-`,
        group_id: (body as { group_id: number }).group_id,
        group_name: "users",
        portal_visible: false,
        valid: true,
        position: CLASSES.length + 1,
        lock_version: 1,
        can_write: true,
        current_definition_version: null,
        definition: null,
      };
      CLASSES.push(cls);
      return ok(cls, 201);
    }
    if (sub === "/catalog" && method === "POST") {
      const b = body as CatalogItemIn;
      const item: CatalogItem = { id: Math.max(0, ...CATALOG.map((c) => c.id)) + 1, catalog_class: b.catalog_class, item_key: b.item_key, name: b.name, functionality: b.functionality ?? null, position: b.position ?? 0, valid: b.valid ?? true };
      CATALOG.push(item);
      return ok(item, 201);
    }
    const catalogPut = /^\/catalog\/(\d+)$/.exec(sub);
    if (catalogPut && method === "PUT") {
      const item = CATALOG.find((c) => c.id === Number(catalogPut[1]));
      if (!item) return notFound();
      const b = body as CatalogItemIn;
      Object.assign(item, { name: b.name, functionality: b.functionality ?? null, position: b.position ?? item.position, valid: b.valid ?? item.valid });
      return ok(item);
    }
    if (sub === "/catalog-classes") return ok([DEMO_CATALOG_DEPLOYMENT, DEMO_CATALOG_INCIDENT, CATALOG_OS]);
    if (sub === "/catalog" && method === "GET") {
      const cls = query.get("catalog_class");
      return ok(cls ? CATALOG.filter((c) => c.catalog_class === cls) : CATALOG);
    }
    if (sub === "/propagation/recompute") return ok({ job_id: 9301, status: "queued" }, 202);
    return undefined;
  }

  const m = /\/api\/v1\/cmdb(\/.*)?$/.exec(pathname);
  if (!m) return undefined;
  const sub = m[1] ?? "";
  const seg = sub.split("/").filter(Boolean);

  if (sub === "/status") {
    const status: CmdbStatus = { mode: demoItsmMode(), readable_class_count: CLASSES.length, can_admin: true, views_present: true };
    return ok(status);
  }
  if (sub === "/classes") return ok(CLASSES);
  if (seg[0] === "classes" && seg[2] === "definitions") {
    const c = CLASSES.find((x) => x.id === Number(seg[1]));
    return c?.definition ? ok(c.definition) : notFound();
  }
  if (sub === "/catalog") {
    const cls = query.get("catalog_class");
    return ok(cls ? CATALOG.filter((c) => c.catalog_class === cls) : CATALOG);
  }
  if (sub === "/form-options") return ok(FORM_OPTIONS);
  if (sub === "/filter-fields") {
    const cid = Number(query.get("class_id"));
    return ok(filterFields(Number.isInteger(cid) && cid > 0 ? cid : undefined));
  }
  if (sub === "/companies/lookup") return ok([{ id: "beispiel", label: "Beispiel GmbH" }]);
  if (sub === "/link-types") return ok(LINK_TYPES);
  if (sub === "/import-templates" && method === "GET") {
    const cid = Number(query.get("class_id"));
    return ok(Number.isInteger(cid) && cid > 0 ? store.templates.filter((x) => x.class_id === cid) : store.templates);
  }
  if (sub === "/import-templates" && method === "POST") {
    const b = (body ?? {}) as ImportTemplateIn;
    const row: ImportTemplate = {
      id: store.nextTemplateId++,
      name: b.name,
      class_id: b.class_id,
      file_format: b.file_format,
      mapping: b.mapping,
      options: b.options,
      shared: b.shared ?? false,
      owned: true,
      owner_user_id: 1,
      lock_version: 1,
      created_at: AT,
      changed_at: AT,
    };
    store.templates.push(row);
    return ok(row, 201);
  }
  if (sub === "/import-jobs" && method === "POST") {
    // multipart: the dry-run flag and `on_error` sit in the JSON `request` field
    const raw = body instanceof FormData ? body.get("request") : null;
    const req = (typeof raw === "string" ? JSON.parse(raw) : {}) as { dry_run?: boolean; options?: { on_error?: string } };
    const skip = req.options?.on_error === "skip";
    const dry = req.dry_run === true;
    const bad = "IP-Adresse: ungültiges Format (Wert „10.0.0.999“)";
    const aborted = !dry && !skip; // the faulty row stops a run with "abort": nothing is written
    const rows = [
      "row;status;ci_number;message",
      aborted ? "2;skipped;;nicht geschrieben (Abbruch)" : "2;created;SRV-0007;",
      aborted ? "3;skipped;;nicht geschrieben (Abbruch)" : "3;created;SRV-0008;",
      aborted ? "4;skipped;;nicht geschrieben (Abbruch)" : "4;updated;SRV-0001;",
      dry || aborted ? `5;error;;${bad}` : `5;skipped;;${bad}`,
      aborted ? "6;skipped;;nicht geschrieben (Abbruch)" : "6;created;SRV-0009;",
    ];
    const counts = dry
      ? { created: 3, updated: 1, unchanged: 0, errors: 1, skipped: 0 }
      : { created: 3, updated: 1, unchanged: 0, errors: 0, skipped: 1 };
    const failure = aborted ? "Die Datei enthält 1 fehlerhafte Zeile(n). Es wurde nichts geschrieben. Einzelheiten stehen im Bericht." : null;
    const jobId = finishedJob(
      store,
      "cmdb_import",
      failure ? null : { ...counts, dry_run: dry },
      [
        { id: 1, name: "input-import.csv", content_type: "text/csv", size_bytes: 256 },
        { id: 2, name: "report.csv", content_type: "text/csv; charset=utf-8", size_bytes: 256 },
      ],
      failure,
    );
    store.reports[jobId] = "\ufeff" + rows.join("\r\n") + "\r\n";
    return ok({ job_id: jobId, status: "queued" }, 202);
  }
  if (sub === "/saved-searches" && method === "GET") return ok(store.saved);
  if (sub === "/saved-searches" && method === "POST") {
    const b = (body ?? {}) as { name: string; shared?: boolean; criteria: CmdbSavedSearch["criteria"] };
    const row: CmdbSavedSearch = {
      id: store.nextSavedId++,
      name: b.name,
      shared: b.shared ?? false,
      criteria: b.criteria,
      owned: true,
      owner_user_id: 1,
      created_at: AT,
      changed_at: AT,
    };
    store.saved.push(row);
    return ok(row, 201);
  }
  if (seg[0] === "saved-searches" && seg[1] && method === "DELETE") {
    store.saved = store.saved.filter((x) => x.id !== Number(seg[1]));
    return none();
  }
  if (sub === "/bulk-jobs" && method === "POST") {
    const b = (body ?? {}) as { ci_ids: number[]; deployment_state_id?: number | null; incident_state_id?: number | null; link?: { link_type_key: string; target_kind: string; target_key: string; ci_side?: string } | null };
    let updated = 0;
    for (const id of b.ci_ids) {
      const ci = store.cis.find((c) => c.id === id);
      if (!ci) continue;
      if (b.deployment_state_id) ci.deployment = b.deployment_state_id;
      if (b.incident_state_id) {
        ci.incidentOwn = b.incident_state_id;
        ci.incidentCurrent = b.incident_state_id;
      }
      if (b.link && b.link.target_kind === "ci") {
        const other = Number(b.link.target_key);
        const link: DemoLink = b.link.ci_side === "target" ? { id: store.nextLinkId++, type: b.link.link_type_key, source: other, target: id } : { id: store.nextLinkId++, type: b.link.link_type_key, source: id, target: other };
        store.links.push(link);
      }
      updated += 1;
    }
    const jobId = finishedJob(store, "cmdb_bulk", { updated, skipped: b.ci_ids.length - updated }, []);
    return ok({ job_id: jobId, status: "queued" }, 202);
  }
  if (sub === "/export-jobs" && method === "POST") {
    const fmt = (body as { file_format?: string } | undefined)?.file_format === "xlsx" ? "xlsx" : "csv";
    const jobId = finishedJob(store, "cmdb_export", null, [
      { id: 1, name: `cmdb.${fmt}`, content_type: fmt === "xlsx" ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" : "text/csv; charset=utf-8", size_bytes: 512 },
    ]);
    return ok({ job_id: jobId, status: "queued" }, 202);
  }
  if (sub === "/attachments" && method === "POST") {
    const file = body instanceof FormData ? body.get("file") : null;
    const ciField = body instanceof FormData ? body.get("ci_id") : null;
    const att: CmdbAttachment = {
      id: store.nextAttachmentId++,
      ci_id: ciField ? Number(ciField) : null,
      filename: file instanceof File ? file.name : "datei.txt",
      content_type: file instanceof File && file.type ? file.type : "application/octet-stream",
      size_bytes: file instanceof File ? file.size : 0,
      created_at: AT,
    };
    return ok(att, 201);
  }
  if (sub === "/cis/search" && method === "POST") return ok(search(store, (body ?? {}) as SearchBody));
  if (sub === "/cis/lookup") {
    const rows = store.cis.filter((c) => matches(c, query.get("q") ?? ""));
    return ok(rows.map((c) => ({ id: c.id, number: c.number, name: c.name, class_key: classOf(c.classId).class_key, class_name: classOf(c.classId).name })));
  }
  if (sub === "/cis" && method === "POST") {
    const b = (body ?? {}) as { class_id: number; name: string; deployment_state_id: number; incident_state_id: number; attributes?: Record<string, AttrEntry[]> };
    const cls = classOf(b.class_id);
    const id = store.nextCiId++;
    const ci: DemoCi = {
      id,
      number: `${cls.number_prefix}-${String(id).padStart(4, "0")}`,
      classId: cls.id,
      name: b.name,
      deployment: b.deployment_state_id,
      incidentOwn: b.incident_state_id,
      incidentCurrent: b.incident_state_id,
      attrs: b.attributes ?? {},
      lockVersion: 1,
    };
    store.cis.push(ci);
    return ok(ciOut(store, ci), 201);
  }
  if (sub === "/links" && method === "POST") {
    const b = (body ?? {}) as { source_kind?: string; source_key: string; target_kind?: string; target_key: string; link_type: string };
    const kindOf = (k: string | undefined) => (k ?? "ci") as LinkOut["other"]["kind"];
    let link: DemoLink;
    if (kindOf(b.source_kind) === "ci" && kindOf(b.target_kind) === "ci") {
      link = { id: store.nextLinkId++, type: b.link_type, source: Number(b.source_key), target: Number(b.target_key) };
    } else {
      // the CI end is the source; the other end is only a label in the demo
      const ciFirst = kindOf(b.source_kind) === "ci";
      const [ciKey, kind, key] = ciFirst ? [b.source_key, kindOf(b.target_kind), b.target_key] : [b.target_key, kindOf(b.source_kind), b.source_key];
      link = { id: store.nextLinkId++, type: b.link_type, source: Number(ciKey), target: 0, ext: { kind, key, label: DEMO_EXT_LABELS[kind]?.[key] ?? `${kind} ${key}`, url: kind === "ticket" ? `/agent/tickets/${key}` : null } };
    }
    store.links.push(link);
    return ok(linkOut(store, link, link.source), 201);
  }
  if (seg[0] === "links" && seg[1] && method === "DELETE") {
    store.links = store.links.filter((l) => l.id !== Number(seg[1]));
    return none();
  }

  if (seg[0] === "cis" && seg[1] && /^\d+$/.test(seg[1])) {
    const ci = store.cis.find((c) => c.id === Number(seg[1]));
    if (!ci) return notFound();
    const part = seg[2];
    if (!part) {
      if (method === "DELETE") {
        store.cis = store.cis.filter((c) => c.id !== ci.id);
        return none();
      }
      if (method === "PUT") {
        const b = (body ?? {}) as { name?: string; deployment_state_id?: number; incident_state_id?: number; attributes?: Record<string, AttrEntry[]>; expected_lock_version?: number };
        if (b.expected_lock_version !== undefined && b.expected_lock_version !== ci.lockVersion) {
          return { status: 409, body: { detail: { code: "conflict", message: "Inzwischen geändert. Bitte neu laden." } } };
        }
        const next = { name: b.name ?? ci.name, deployment: b.deployment_state_id ?? ci.deployment, incidentOwn: b.incident_state_id ?? ci.incidentOwn, attrs: b.attributes ?? ci.attrs };
        const same = next.name === ci.name && next.deployment === ci.deployment && next.incidentOwn === ci.incidentOwn && JSON.stringify(next.attrs) === JSON.stringify(ci.attrs);
        if (same) return ok({ ci: ciOut(store, ci), changed: false });
        Object.assign(ci, next);
        ci.lockVersion += 1;
        return ok({ ci: ciOut(store, ci), changed: true });
      }
      return ok(ciOut(store, ci));
    }
    if (part === "versions" && seg[3]) {
      const vid = Number(seg[3]);
      return versionIds(ci).includes(vid) ? ok(versionOut(store, ci, vid)) : notFound();
    }
    if (part === "versions") {
      const before = Number(query.get("before_id")) || Infinity;
      const limit = Number(query.get("limit")) || 20;
      const all: CiVersionSummary[] = versionIds(ci)
        .map((id) => ({
          id,
          definition_id: classOf(ci.classId).definition?.id ?? 1,
          definition_version: 1,
          name: ci.name,
          deployment_state_id: ci.deployment,
          incident_state_id: ci.incidentOwn,
          deployment_state: catalogItem(ci.deployment).name,
          incident_state: catalogItem(ci.incidentOwn).name,
          change_note: null,
          created_by: 1,
          created_by_name: "Ada Agent",
          created_at: AT,
          archived: false,
        }))
        .reverse()
        .filter((v) => v.id < before);
      return ok({ items: all.slice(0, limit), has_more: all.length > limit, archived_count: 0 });
    }
    if (part === "diff") {
      const ids = versionIds(ci);
      return ok({
        from_version_id: ids[0],
        to_version_id: ids[ids.length - 1],
        rows: ids.length > 1 ? [{ kind: "changed", path: "ip", label: "IP-Adresse", old: "192.0.2.10", new: String(ci.attrs.ip?.[0]?.v ?? "") }] : [],
      });
    }
    if (part === "history") {
      const before = Number(query.get("before_id")) || Infinity;
      const limit = Number(query.get("limit")) || 25;
      const all = versionIds(ci)
        .map((vid, i) => ({ id: vid, event: i === 0 ? "ConfigItemCreate" : "VersionCreate", detail: i === 0 ? ci.number : String(i + 1), version_id: vid, created_by: 1, created_by_name: "Ada Agent", created_at: AT }))
        .reverse()
        .filter((h) => h.id < before);
      const page: CiHistoryPage = { has_more: all.length > limit, items: all.slice(0, limit) };
      return ok(page);
    }
    if (part === "links") return ok(store.links.filter((l) => l.source === ci.id || (!l.ext && l.target === ci.id)).map((l) => linkOut(store, l, ci.id)));
    if (part === "tickets") return ok(ci.id === 3 ? [...TICKETS, CLOSED_TICKET].filter((tk) => query.get("open_only") !== "true" || tk.open) : []);
    if (part === "impact") return ok(impact(store, ci.id));
    if (part === "affected") {
      const affected: Affected =
        ci.id === 3
          ? { ci_count: 2, service_count: 0, customer_count: 1, open_ticket_count: 1, truncated: false, services: [], customers: [{ id: "beispiel", label: "Beispiel GmbH" }], open_tickets: TICKETS }
          : { ci_count: 0, service_count: 0, customer_count: 0, open_ticket_count: 0, truncated: false, services: [], customers: [], open_tickets: [] };
      return ok(affected);
    }
    if (part === "attachments") return ok([]);
  }
  return undefined;
}
