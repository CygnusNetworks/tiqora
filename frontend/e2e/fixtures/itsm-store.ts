/**
 * Stateful ITSM answers for the e2e mock (`mock-api.ts`).
 *
 * The demo resolver (`src/demo/itsmMock.ts`) is read-only and answers one fixed scenario. The e2e
 * flows write (link a CI, link incidents, declare a major incident, apply the setup, edit the
 * matrix) and read the result back, so this store keeps a small scenario per test and answers the
 * ITSM routes that change it. Everything else falls through to `resolveItsmData`.
 *
 * Scenario (all names example data):
 *  - 100 "Website nicht erreichbar": incident, linked to 103 and 104, CI 201 linked, service
 *    "Kundenportal" (incident state Störung)
 *  - 101 "Defekter Switch-Port": problem, no incidents yet
 *  - 102 "Portal antwortet langsam", 105 "Login-Seite lädt nicht": incidents to link to the problem
 *  - 103 "Drucker im Erdgeschoss", 104 "VPN-Zugang gestört": tickets linked to 100
 */
import type {
  CiSummary,
  CiTicketLink,
  MajorIncident,
  PriorityMatrix,
  Problem,
  SetupPlan,
  TicketItsm,
} from "../../src/lib/itsmApi";
import { demoCis, resolveItsmData } from "../../src/demo/itsmMock";
import type { DemoItsmMode } from "../../src/demo/itsmMode";

export type ItsmReply = { status: number; body?: unknown };

type DirTicket = { tn: string; title: string; state: string; state_type: string };
type LinkRow = { a: number; b: number; type: string };
type SetupItem = SetupPlan["items"][number];

const AT = "2026-10-12T08:00:00Z";

const DIRECTORY: Record<number, DirTicket> = {
  100: { tn: "20261012000", title: "Website nicht erreichbar", state: "open", state_type: "open" },
  101: { tn: "20261012001", title: "Defekter Switch-Port", state: "open", state_type: "open" },
  102: { tn: "20261012002", title: "Portal antwortet langsam", state: "new", state_type: "new" },
  103: { tn: "20261012003", title: "Drucker im Erdgeschoss", state: "open", state_type: "open" },
  104: { tn: "20261012004", title: "VPN-Zugang gestört", state: "open", state_type: "open" },
  105: { tn: "20261012005", title: "Login-Seite lädt nicht", state: "new", state_type: "new" },
};

// Same ids as the demo matrix: id 1 is the most urgent priority, id 5 the least.
export const ITSM_PRIORITIES = [
  { id: 1, name: "5 very high" },
  { id: 2, name: "4 high" },
  { id: 3, name: "3 normal" },
  { id: 4, name: "2 low" },
  { id: 5, name: "1 very low" },
];

export const NOTE_JOB_ID = 9501;
export const CLOSE_JOB_ID = 9503;

type Store = {
  cis: Map<number, CiTicketLink[]>;
  links: LinkRow[];
  problem: Problem;
  majors: Map<number, MajorIncident>;
  jobs: Map<number, Record<string, unknown>>;
  setup: SetupItem[];
  matrixCells: PriorityMatrix["cells"] | null;
  updateSeq: number;
};

function freshSetup(): SetupItem[] {
  const item = (group: string, kind: string, name: string, status: string): SetupItem => ({ group, kind, name, status, detail: null });
  return [
    item("incident_problem", "ticket_type", "Incident", "present"),
    item("incident_problem", "ticket_type", "Problem", "present"),
    item("incident_problem", "dynamic_field", "ITSMCriticality", "missing"),
    item("incident_problem", "dynamic_field", "ITSMImpact", "missing"),
    item("change", "ticket_type", "Change", "missing"),
    item("change", "dynamic_field", "ITSMDecisionResult", "missing"),
  ];
}

function fresh(): Store {
  return {
    cis: new Map([[100, [{ ticket_id: 100, ci: demoCis[0], link_type: "RelevantTo", direction: "both", created_at: AT }]]]),
    links: [
      { a: 100, b: 103, type: "Normal" },
      { a: 100, b: 104, type: "Normal" },
    ],
    problem: {
      ticket_id: 101, known_error: false, known_error_since: null, known_error_article_id: null, known_error_article_state: null,
      root_cause: "Defekter Switch-Port im Rack 3", workaround: "Server auf Port 12 umgesteckt", change_by: 1, change_time: AT,
      incidents: [],
    },
    majors: new Map(),
    jobs: new Map(),
    setup: freshSetup(),
    matrixCells: null,
    updateSeq: 0,
  };
}

let store: Store = fresh();

/** Called once per test, like the other module-level mock state of `mockApi`. */
export function resetItsmStore(): void {
  store = fresh();
}

/** Test setup: a major incident that is already active (for the screenshots). */
export function seedMajorIncident(ticketId: number, summary: string): void {
  store.majors.set(ticketId, newMajor(ticketId, summary));
}

/** Test setup: link a demo CI to a ticket without going through the UI. */
export function seedTicketCi(ticketId: number, ciId: number): void {
  const found = demoCis.find((c) => c.id === ciId);
  if (found) addCi(ticketId, found, "RelevantTo");
}

function newMajor(ticketId: number, summary: string | null): MajorIncident {
  return {
    ticket_id: ticketId, active: true, declared_at: AT, declared_by: 1, ended_at: null, ended_by: null, summary,
    timeline: [{ kind: "declared", at: AT, by: 1, summary, target_count: 0 }],
  };
}

function addCi(ticketId: number, ci: CiSummary, linkType: string): CiTicketLink {
  const row: CiTicketLink = { ticket_id: ticketId, ci, link_type: linkType, direction: "both", created_at: AT };
  store.cis.set(ticketId, [...(store.cis.get(ticketId) ?? []), row]);
  return row;
}

function linkedIds(ticketId: number): number[] {
  const out = new Set<number>();
  for (const l of store.links) {
    if (l.a === ticketId) out.add(l.b);
    else if (l.b === ticketId) out.add(l.a);
  }
  return [...out];
}

function ticketLinks(ticketId: number) {
  return store.links
    .filter((l) => l.a === ticketId || l.b === ticketId)
    .map((l) => {
      const other = l.a === ticketId ? l.b : l.a;
      const d = DIRECTORY[other];
      return {
        link_type: l.type, source_key: "Ticket", target_key: "Ticket", state: "valid", other_ticket_id: other,
        other_tn: d?.tn ?? String(other), other_title: d?.title ?? null, other_state: d?.state ?? null,
        other_state_type: d?.state_type ?? null, other_role: null,
      };
    });
}

function ticketItsm(id: number, mode: DemoItsmMode): TicketItsm {
  const base = resolveItsmData(`/api/v1/tickets/${id}/itsm`, "GET", "", mode) as TicketItsm;
  const own = store.majors.get(id) ?? null;
  const banner = [...store.majors.values()].find((m) => m.active && m.ticket_id !== id && linkedIds(m.ticket_id).includes(id));
  const role = id === 101 ? "problem" : own?.active ? "major_incident" : "incident";
  return {
    ...base,
    role,
    ci_count: (store.cis.get(id) ?? []).length,
    problem: null,
    major_incident: own,
    major_banner: banner
      ? {
          ticket_id: banner.ticket_id,
          tn: DIRECTORY[banner.ticket_id]?.tn ?? String(banner.ticket_id),
          title: DIRECTORY[banner.ticket_id]?.title ?? "",
          summary: banner.summary ?? null,
          declared_at: banner.declared_at,
        }
      : null,
  };
}

function job(id: number, kind: string, result: Record<string, unknown>) {
  return {
    id, kind, title_key: `jobs.kinds.${kind}`, status: "succeeded", progress_done: 1, progress_total: 1, progress_message: null,
    cancel_requested: false, result, error: null, files: [], created_by: 1,
    created_at: AT, started_at: AT, finished_at: AT,
  };
}

function incidentRow(id: number, closed: boolean) {
  const d = DIRECTORY[id];
  return {
    ticket_id: id, tn: d?.tn ?? String(id), title: d?.title ?? "", state: closed ? "closed successful" : d?.state ?? "open",
    state_type: closed ? "closed" : d?.state_type ?? "open", type_name: "Incident", closed,
  };
}

function adminMatrix(mode: DemoItsmMode): PriorityMatrix {
  const base = resolveItsmData("/api/v1/admin/itsm/priority-matrix", "GET", "", mode) as PriorityMatrix;
  return mode === "tiqora" && store.matrixCells ? { ...base, cells: store.matrixCells } : base;
}

function setupPlan(): SetupPlan {
  return { complete: store.setup.every((i) => i.status === "present"), items: store.setup };
}

type Body = Record<string, unknown> | undefined;

/**
 * Answers the ITSM routes the e2e flows touch; `undefined` means "not mine" (the caller then asks
 * the read-only demo resolver). `search` is the query string of the request.
 */
export function itsmStoreReply(path: string, method: string, search: string, body: Body, mode: DemoItsmMode): ItsmReply | undefined {
  const params = new URLSearchParams(search);

  // ---- tickets ---------------------------------------------------------------------------
  if (path.endsWith("/api/v1/tickets/search") && method === "GET") {
    const q = (params.get("q") ?? "").toLowerCase();
    const hits = Object.entries(DIRECTORY)
      .filter(([, d]) => q && (d.tn.includes(q) || d.title.toLowerCase().includes(q)))
      .map(([id, d]) => ({ ticket_id: Number(id), tn: d.tn, title: d.title, queue: "Raw", state: d.state, state_type: d.state_type }));
    return { status: 200, body: hits.slice(0, Number(params.get("limit") ?? 10)) };
  }
  const jobAt = path.match(/\/api\/v1\/jobs\/(\d+)$/);
  if (jobAt && method === "GET" && store.jobs.has(Number(jobAt[1]))) {
    const id = Number(jobAt[1]);
    return { status: 200, body: job(id, id === CLOSE_JOB_ID ? "itsm.problem_close" : "itsm.linked_articles", store.jobs.get(id) ?? {}) };
  }

  const t = path.match(/\/api\/v1\/tickets\/(\d+)\/(.+)$/);
  if (t) {
    const id = Number(t[1]);
    const rest = t[2];
    if (rest === "links" && method === "GET") return { status: 200, body: ticketLinks(id) };
    const unlink = rest.match(/^links\/(\d+)$/);
    if (unlink && method === "DELETE") {
      const other = Number(unlink[1]);
      store.links = store.links.filter((l) => !((l.a === id && l.b === other) || (l.a === other && l.b === id)));
      return { status: 204 };
    }
    if (rest === "itsm" && method === "GET") return { status: 200, body: ticketItsm(id, mode) };

    if (rest === "cis" && method === "GET") return { status: 200, body: store.cis.get(id) ?? [] };
    if (rest === "cis" && method === "POST") {
      const ref = String(body?.ci ?? "");
      const found = demoCis.find((c) => c.ref === ref);
      if (!found) return { status: 404, body: { detail: { code: "ci_not_found" } } };
      return { status: 201, body: addCi(id, found, String(body?.link_type ?? "RelevantTo")) };
    }
    const unlinkCi = rest.match(/^cis\/(znuny|tiqora)\/(\d+)$/);
    if (unlinkCi && method === "DELETE") {
      store.cis.set(id, (store.cis.get(id) ?? []).filter((l) => !(l.ci.source === unlinkCi[1] && l.ci.id === Number(unlinkCi[2]))));
      return { status: 204 };
    }

    if (rest === "linked-articles" && method === "POST") {
      const targets = (body?.target_ticket_ids as number[] | undefined) ?? [];
      const [first, ...others] = targets;
      store.jobs.set(NOTE_JOB_ID, {
        done: first === undefined ? [] : [first],
        skipped: others.map((ticket_id) => ({ ticket_id, reason: "no_rw" })),
      });
      return { status: 202, body: { job_id: NOTE_JOB_ID, status: "queued" } };
    }

    if (rest === "problem" && method === "GET") return { status: 200, body: { ...store.problem, ticket_id: id } };
    if (rest === "problem" && method === "PUT") {
      store.problem = { ...store.problem, root_cause: (body?.root_cause as string | null) ?? null, workaround: (body?.workaround as string | null) ?? null };
      return { status: 200, body: store.problem };
    }
    if (rest === "problem/incidents" && method === "POST") {
      const ids = (body?.incident_ticket_ids as number[] | undefined) ?? [];
      for (const incident of ids) {
        if (store.problem.incidents.some((i) => i.ticket_id === incident)) continue;
        store.problem = { ...store.problem, incidents: [...store.problem.incidents, incidentRow(incident, false)] };
        store.links.push({ a: id, b: incident, type: "ParentChild" });
      }
      return { status: 200, body: store.problem };
    }
    if (rest === "problem/known-error" && method === "POST") {
      store.problem = {
        ...store.problem, known_error: true, known_error_since: AT, known_error_article_id: 700, known_error_article_state: "published",
      };
      return { status: 200, body: store.problem };
    }
    if (rest === "problem/close-incidents" && method === "POST") {
      const open = store.problem.incidents.filter((i) => !i.closed).map((i) => i.ticket_id);
      store.problem = { ...store.problem, incidents: store.problem.incidents.map((i) => incidentRow(i.ticket_id, true)) };
      store.jobs.set(CLOSE_JOB_ID, { done: open, skipped: [] });
      return { status: 202, body: { job_id: CLOSE_JOB_ID, status: "queued" } };
    }

    if (rest === "major-incident" && method === "GET") {
      const major = store.majors.get(id);
      return major ? { status: 200, body: major } : { status: 404, body: { detail: { code: "not_found" } } };
    }
    if (rest === "major-incident" && method === "POST") {
      const made = newMajor(id, ((body?.summary as string | null) ?? null) || null);
      store.majors.set(id, made);
      return { status: 201, body: made };
    }
    if (rest === "major-incident" && method === "DELETE") {
      const major = store.majors.get(id);
      if (!major) return { status: 404, body: { detail: { code: "not_found" } } };
      const ended: MajorIncident = {
        ...major, active: false, ended_at: AT, ended_by: 1,
        timeline: [...major.timeline, { kind: "ended", at: AT, by: 1, target_count: 0 }],
      };
      store.majors.set(id, ended);
      return { status: 200, body: ended };
    }
    if (rest === "major-incident/updates" && method === "POST") {
      const major = store.majors.get(id);
      if (!major) return { status: 404, body: { detail: { code: "not_found" } } };
      store.updateSeq += 1;
      const mode_ = String(body?.mode ?? "internal");
      const entry = {
        kind: "update" as const, at: AT, by: 1, mode: mode_, body: String(body?.body_html ?? ""),
        target_count: mode_ === "internal" ? 0 : linkedIds(id).length, update_id: store.updateSeq,
      };
      store.majors.set(id, { ...major, timeline: [...major.timeline, entry] });
      return { status: 201, body: { update: entry, job_id: null } };
    }
    return undefined;
  }

  // ---- admin -----------------------------------------------------------------------------
  if (path.endsWith("/api/v1/admin/priorities") && method === "GET") {
    const items = ITSM_PRIORITIES.map((p) => ({ ...p, valid_id: 1, create_time: AT, change_time: AT }));
    return { status: 200, body: { items, total: items.length, page: 1, page_size: 500 } };
  }
  if (path.endsWith("/api/v1/admin/itsm/setup")) {
    if (method === "GET") return { status: 200, body: setupPlan() };
    if (method === "POST") {
      const groups = (body?.groups as string[] | undefined) ?? [];
      store.setup = store.setup.map((i) => (groups.includes(i.group) ? { ...i, status: "present" } : i));
      return { status: 200, body: setupPlan() };
    }
  }
  if (path.endsWith("/api/v1/admin/itsm/priority-matrix")) {
    if (method === "GET") return { status: 200, body: adminMatrix(mode) };
    if (method === "PUT") {
      if (mode !== "tiqora") return { status: 409, body: { detail: { code: "matrix_read_only" } } };
      store.matrixCells = (body?.cells as PriorityMatrix["cells"]) ?? [];
      return { status: 200, body: adminMatrix(mode) };
    }
  }
  return undefined;
}
