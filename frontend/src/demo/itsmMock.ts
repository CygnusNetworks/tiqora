/**
 * Demo / screenshot data for the ITSM core (`/api/v1/itsm/*`, ticket CIs, problem, major incident).
 * Path-scoped like `giMock.ts`: answers only its own paths and returns `undefined` for everything
 * else, so `resolveData` falls through. Mode A data (Znuny read-through): three CIs, a problem
 * with two incidents and an active major incident. `/itsm/status` answers from `demoItsmMode()`;
 * E2E tests for Mode A set `localStorage["tiqora.demo.itsmMode"] = "znuny_readthrough"` first.
 *
 * All example data uses example.org / .test only.
 */
import type {
  CatalogValue,
  CiAttribute,
  CiDetail,
  CiPage,
  CiSummary,
  CiTicketLink,
  CiVersion,
  ItsmCatalog,
  ItsmStatus,
  LinkGraph,
  LinkTypeOption,
  MajorIncident,
  PriorityMatrix,
  Problem,
  ServiceIncidentState,
  ServiceItsm,
  TicketItsm,
  TicketLinkTarget,
} from "@/lib/itsmApi";
import { demoItsmMode, type DemoItsmMode } from "./itsmMode";

const T0 = "2026-10-11T08:00:00Z";

const DEPL: CatalogValue[] = [
  { key: "31", name: "Produktiv", functionality: "productive" },
  { key: "32", name: "Im Test", functionality: "preproductive" },
  { key: "33", name: "Außer Betrieb", functionality: "postproductive" },
];
const INCI: CatalogValue[] = [
  { key: "41", name: "Betriebsbereit", functionality: "operational" },
  { key: "42", name: "Eingeschränkt", functionality: "warning" },
  { key: "43", name: "Störung", functionality: "incident" },
];

export const demoItsmCatalog: ItsmCatalog = {
  classes: [
    { key: "11", name: "Computer", icon: null, can_read: true, can_write: false },
    { key: "12", name: "Netzwerk", icon: null, can_read: true, can_write: false },
    { key: "13", name: "Software", icon: null, can_read: true, can_write: false },
  ],
  deployment_states: DEPL,
  incident_states: INCI,
};

function ci(id: number, number: string, name: string, cls: string, clsName: string, depl: number, inci: number): CiSummary {
  return {
    id, source: "znuny", ref: `znuny:${id}`, number, name, class_key: cls, class_name: clsName,
    deployment_state: DEPL[depl], incident_state: INCI[inci], changed_at: T0,
    edit_url: `https://znuny.example.org/znuny/index.pl?Action=AgentITSMConfigItemEdit;ConfigItemID=${id}`,
  };
}

export const demoCis: CiSummary[] = [
  ci(201, "1002001", "srv-web-01.example.org", "11", "Computer", 0, 2),
  ci(202, "1002002", "sw-core-01.example.org", "12", "Netzwerk", 0, 1),
  ci(203, "1002003", "Mailserver Postfix", "13", "Software", 0, 0),
];

const VERSIONS: CiVersion[] = [
  { id: 903, seq: 3, name: "srv-web-01.example.org", deployment_state: DEPL[0], incident_state: INCI[2], created_at: T0, created_by: 1, created_by_name: "Alex Turner", change_note: "Störung gemeldet" },
  { id: 902, seq: 2, name: "srv-web-01.example.org", deployment_state: DEPL[0], incident_state: INCI[0], created_at: "2026-09-01T08:00:00Z", created_by: 1, created_by_name: "Alex Turner", change_note: "RAM erweitert" },
  { id: 901, seq: 1, name: "srv-web-01.example.org", deployment_state: DEPL[1], incident_state: INCI[0], created_at: "2026-06-01T08:00:00Z", created_by: 1, created_by_name: "Alex Turner", change_note: null },
];

function attr(a: Omit<CiAttribute, "children"> & { children?: CiAttribute[] }): CiAttribute {
  return { ...a, children: a.children ?? [] };
}

function detail(summary: CiSummary, version: CiVersion | null): CiDetail {
  return {
    summary,
    version,
    definition_version: 4,
    read_only: true,
    attributes: [
      attr({ key: "Description", label: "Beschreibung", kind: "textarea", display: "Webserver der Kundenportale", raw: "Webserver der Kundenportale", ref: null }),
      attr({
        key: "NIC", label: "Netzwerkkarte", kind: "other", display: "", raw: null, ref: null,
        children: [
          attr({
            key: "NIC_1", label: "Netzwerkkarte 1", kind: "text", display: "eth0", raw: "eth0", ref: null,
            children: [attr({ key: "IPAddress", label: "IP-Adresse", kind: "ip", display: "192.0.2.10", raw: "192.0.2.10", ref: null })],
          }),
          attr({
            key: "NIC_2", label: "Netzwerkkarte 2", kind: "text", display: "eth1", raw: "eth1", ref: null,
            children: [attr({ key: "IPAddress", label: "IP-Adresse", kind: "ip", display: "192.0.2.11", raw: "192.0.2.11", ref: null })],
          }),
        ],
      }),
      attr({ key: "ConnectedTo", label: "Verbunden mit", kind: "ci", display: "sw-core-01.example.org", raw: "202", ref: "znuny:202" }),
      attr({ key: "Wiki", label: "Dokumentation", kind: "url", display: "https://wiki.example.org/srv-web-01", raw: "https://wiki.example.org/srv-web-01", ref: null }),
    ],
  };
}

const LINKS: CiTicketLink[] = [
  { ticket_id: 100, ci: demoCis[0], link_type: "RelevantTo", direction: "both", created_at: T0 },
  { ticket_id: 101, ci: demoCis[1], link_type: "RelevantTo", direction: "both", created_at: T0 },
];

const CI_TICKETS: TicketLinkTarget[] = [
  {
    link_type: "RelevantTo", source_key: "Ticket", target_key: "ITSMConfigItem", state: "open",
    other_ticket_id: 100, other_tn: "20260701000", other_title: "Website nicht erreichbar", other_state: "open", other_state_type: "open", other_role: "incident",
  },
];

const MATRIX: PriorityMatrix = {
  source: "znuny", enabled: true, editable: false,
  criticality_values: ["1 sehr niedrig", "3 normal", "5 sehr hoch"],
  impact_values: ["1 sehr niedrig", "3 normal", "5 sehr hoch"],
  cells: [
    { criticality: "1 sehr niedrig", impact: "1 sehr niedrig", priority_id: 5 },
    { criticality: "3 normal", impact: "3 normal", priority_id: 3 },
    { criticality: "5 sehr hoch", impact: "5 sehr hoch", priority_id: 1 },
  ],
};

const LINK_TYPES: LinkTypeOption[] = [
  { name: "Normal", origin: "znuny", pointed: false, propagation: "none", source_name: "Normal", target_name: "Normal", source_label: "Normal", target_label: "Normal" },
  { name: "ParentChild", origin: "znuny", pointed: true, propagation: "none", source_name: "Parent", target_name: "Child", source_label: "Übergeordnet", target_label: "Untergeordnet" },
  { name: "DependsOn", origin: "znuny", pointed: true, propagation: "source", source_name: "Depends on", target_name: "Dependency of", source_label: "Hängt ab von", target_label: "Voraussetzung für" },
];

const PROBLEM: Problem = {
  ticket_id: 101, known_error: false, known_error_since: null, known_error_article_id: null, known_error_article_state: null,
  root_cause: "Defekter Switch-Port im Rack 3", workaround: "Server auf Port 12 umgesteckt", change_by: 1, change_time: T0,
  incidents: [
    { ticket_id: 100, tn: "20260701000", title: "Website nicht erreichbar", state: "open", state_type: "open", type_name: "Incident", closed: false },
    { ticket_id: 102, tn: "20260701002", title: "Portal antwortet langsam", state: "new", state_type: "new", type_name: "Incident", closed: false },
  ],
};

const MAJOR: MajorIncident = {
  ticket_id: 100, active: true, declared_at: T0, declared_by: 1, ended_at: null, ended_by: null,
  summary: "Kundenportale nicht erreichbar",
  timeline: [
    { kind: "declared", at: T0, by: 1, summary: "Kundenportale nicht erreichbar", target_count: 0 },
    { kind: "update", at: "2026-10-11T08:30:00Z", by: 1, mode: "internal", body: "<p>Ursache eingegrenzt, Switch wird getauscht.</p>", target_count: 2, update_id: 1 },
  ],
};

const SERVICE: ServiceItsm = {
  service_id: 1, name: "Kundenportal", service_type: "Kundenservice", criticality: "5 sehr hoch", description: "Portal für Kunden",
  owner_user_id: 1, owner_name: "Alex Turner", portal_visible: true, znuny_owned: true,
  min_minutes_between_incidents: null, effective_min_minutes_between_incidents: 30, znuny_sla_min_times: {},
  incident_state: { service_id: 1, state: INCI[2], cis_incident: 1, cis_warning: 1 },
};

function ticketItsm(id: number, mode: DemoItsmMode): TicketItsm {
  const base: TicketItsm = {
    enabled: mode !== "off", mode, role: null, criticality: null, impact: null, matrix_priority_id: null,
    ci_count: LINKS.filter((l) => l.ticket_id === id).length, service: null, recent_incident: null,
    problem: null, major_incident: null, major_banner: null, dialogs: { itsm_fields: false, decision: false },
    known_error_category_configured: true, major_incident_customer_reply: false,
  };
  if (id === 100) {
    return {
      ...base, role: "major_incident", criticality: "5 sehr hoch", impact: "5 sehr hoch", matrix_priority_id: 1, service: SERVICE,
      problem: { ticket_id: 101, tn: "20260701001", title: "Defekter Switch-Port", known_error: false },
      major_incident: MAJOR, dialogs: { itsm_fields: true, decision: false },
    };
  }
  if (id === 101) return { ...base, role: "problem" };
  if (id === 102) {
    return {
      ...base, role: "incident",
      major_banner: { ticket_id: 100, tn: "20260701000", title: "Website nicht erreichbar", summary: MAJOR.summary ?? null, declared_at: T0 },
    };
  }
  return base;
}

function graph(id: number): LinkGraph {
  return {
    root: `ticket:${id}`, truncated: false,
    nodes: [
      { key: `ticket:${id}`, kind: "ticket", number: "20260701000", title: "Website nicht erreichbar", state: "open", state_type: "open", depth: 0, class_name: null, incident_state: null, incident_functionality: null },
      { key: "ticket:101", kind: "ticket", number: "20260701001", title: "Defekter Switch-Port", state: "open", state_type: "open", depth: 1, class_name: null, incident_state: null, incident_functionality: null },
      { key: "ci:znuny:201", kind: "ci", number: "1002001", title: "srv-web-01.example.org", state: null, state_type: null, depth: 1, class_name: "Computer", incident_state: "Störung", incident_functionality: "incident" },
    ],
    edges: [
      { source: `ticket:${id}`, target: "ticket:101", link_type: "ParentChild", kind: "ticket_ticket" },
      { source: `ticket:${id}`, target: "ci:znuny:201", link_type: "RelevantTo", kind: "ticket_ci" },
    ],
  };
}

function status(mode: DemoItsmMode): ItsmStatus {
  return {
    mode, source: "auto", setup_complete: true,
    ci_available: mode !== "off",
    ci_read_only: mode === "znuny_readthrough",
    ci_source: mode === "off" ? null : mode === "tiqora" ? "tiqora" : "znuny",
    unavailable_code: null,
    znuny: { core_installed: true, cmdb_installed: true, profile: "znuny-6.5-itsm", packages: { ITSMCore: "6.5.1", ITSMConfigurationManagement: "6.5.1" } },
  };
}

function ciPage(params: URLSearchParams): CiPage {
  const q = (params.get("q") ?? "").toLowerCase();
  const classes = params.getAll("class");
  const depl = params.getAll("depl");
  const inci = params.getAll("inci");
  const items = demoCis.filter(
    (c) =>
      (!q || c.name.toLowerCase().includes(q) || c.number.includes(q)) &&
      (!classes.length || classes.includes(c.class_key)) &&
      (!depl.length || (c.deployment_state && depl.includes(c.deployment_state.key))) &&
      (!inci.length || (c.incident_state && inci.includes(c.incident_state.key))),
  );
  return { items, total: items.length, truncated: false };
}

const ADMIN_LINK_TYPES = LINK_TYPES.map((o, i) => ({
  id: i + 1, name: o.name, origin: o.origin, source_label: o.source_label, target_label: o.target_label, pointed: o.pointed,
  propagation: o.propagation, offer_ticket_ticket: true, offer_ticket_ci: i === 0, offer_ci_ci: true, offer_ci_service: false,
  position: i, valid: true, znuny_source_name: o.source_name, znuny_target_name: o.target_name,
}));

/** `/api/v1/admin/itsm/*` for the admin pages (read answers, writes echo the current data). */
function resolveAdminItsm(path: string, method: string, mode: DemoItsmMode): unknown | undefined {
  const at = path.indexOf("/api/v1/admin/itsm");
  if (at < 0) return undefined;
  const rest = path.slice(at + "/api/v1/admin/itsm".length);
  if (rest === "" || rest === "/mode") {
    const st = status(mode);
    return {
      mode, source: "auto", stored: false, changed_at: null, changed_by: null, import_run_id: null, previous_mode: null,
      switched: false, switch: null, cmdb_empty: true,
      detected: { ...st.znuny, change_installed: false, unsupported_reason: null },
      allowed_transitions: mode === "off" ? ["znuny_readthrough"] : ["off"],
    };
  }
  if (rest === "/setup") {
    return {
      complete: true,
      items: [
        { group: "incident_problem", kind: "ticket_type", name: "Incident", status: "present", detail: null },
        { group: "incident_problem", kind: "ticket_type", name: "Problem", status: "present", detail: null },
        { group: "incident_problem", kind: "dynamic_field", name: "ITSMCriticality", status: "present", detail: null },
        { group: "change", kind: "ticket_type", name: "Change", status: "missing", detail: null },
      ],
    };
  }
  if (rest === "/priority-matrix") {
    return { ...MATRIX, source: mode === "tiqora" ? "tiqora" : "znuny", editable: mode === "tiqora", enabled: true };
  }
  if (rest === "/link-types") return { items: ADMIN_LINK_TYPES, missing_from_table: [] };
  if (rest === "/link-types/sync") return { changed: 0 };
  const link = rest.match(/^\/link-types\/(\d+)$/);
  if (link && method === "PATCH") return ADMIN_LINK_TYPES.find((l) => l.id === Number(link[1]));
  if (/^\/services\/\d+\/meta$/.test(rest)) return { ...SERVICE, znuny_owned: mode === "znuny_readthrough" };
  return undefined;
}

/**
 * Answers the ITSM demo paths; `search` is the query string of the request (optional). `mode`
 * defaults to the demo build's stored mode; the e2e fixture runs in Node and passes the page's.
 */
export function resolveItsmData(
  path: string,
  method: string,
  search = "",
  mode: DemoItsmMode = demoItsmMode(),
): unknown | undefined {
  const params = new URLSearchParams(search);
  const admin = resolveAdminItsm(path, method, mode);
  if (admin !== undefined) return admin;
  if (path.endsWith("/api/v1/itsm/status")) return status(mode);
  if (path.endsWith("/api/v1/itsm/catalog")) return demoItsmCatalog;
  if (path.endsWith("/api/v1/itsm/cis") && method === "GET") return ciPage(params);
  const ciAt = path.match(/\/api\/v1\/itsm\/cis\/(znuny|tiqora)\/(\d+)(\/versions|\/tickets)?$/);
  if (ciAt) {
    const found = demoCis.find((c) => c.id === Number(ciAt[2]));
    if (!found) return undefined;
    if (ciAt[3] === "/versions") return { items: found.id === 201 ? VERSIONS : VERSIONS.slice(0, 1), total: found.id === 201 ? 3 : 1 };
    if (ciAt[3] === "/tickets") return found.id === 201 ? CI_TICKETS : [];
    const wanted = Number(params.get("version_id"));
    const version = VERSIONS.find((v) => v.id === wanted) ?? VERSIONS[0];
    return detail(found, version);
  }
  if (path.endsWith("/api/v1/itsm/link-types")) return LINK_TYPES;
  if (path.endsWith("/api/v1/itsm/priority-matrix")) return MATRIX;
  if (path.endsWith("/api/v1/itsm/priority-suggestion")) return { priority_id: params.get("criticality") ? 2 : null };
  if (path.endsWith("/api/v1/itsm/services/incident-states")) {
    const out: Record<string, ServiceIncidentState> = {};
    for (const id of params.getAll("ids")) out[id] = SERVICE.incident_state as ServiceIncidentState;
    return out;
  }
  if (/\/api\/v1\/itsm\/services\/\d+\/recent-incident$/.test(path)) return null;
  if (/\/api\/v1\/itsm\/services\/\d+$/.test(path)) return SERVICE;

  const t = path.match(/\/api\/v1\/tickets\/(\d+)\/(cis|itsm|link-graph|problem|major-incident)$/);
  if (t && method === "GET") {
    const id = Number(t[1]);
    if (t[2] === "cis") return LINKS.filter((l) => l.ticket_id === id);
    if (t[2] === "itsm") return ticketItsm(id, mode);
    if (t[2] === "link-graph") return graph(id);
    if (t[2] === "problem") return { ...PROBLEM, ticket_id: id };
    return { ...MAJOR, ticket_id: id };
  }
  return undefined;
}
