import { test, type Route } from "@playwright/test";
import {
  DEMO_APPROVAL_LINK_TOKEN,
  inboxItemOf,
  resolveApprovalLink,
  resolveApprovalsData,
  summaryOf,
} from "../../src/demo/approvalsMock";
import type { ApprovalDelegation, ApprovalDetail } from "../../src/lib/approvalsApi";
import type { ApprovalTemplate } from "../../src/lib/approvalsAdminApi";
import type { PortalApproval, PortalApprovalItem } from "../../src/lib/portalApprovalsApi";

/**
 * Stateful approvals for the e2e mocks: a copy of the demo records (`src/demo/approvalsMock.ts`) that
 * decisions, requests, link redemption, portal votes and admin writes change, so a second request of the
 * same test sees the result (badge decrements, the zoom panel shows "Freigegeben", a used link answers
 * `link_used`). One store per test attempt; `mock-api`, `mock-portal-api` and `mock-admin-api` all ask it.
 */

export type StoreAnswer = { status: number; body: unknown };

const ok = (body: unknown, status = 200): StoreAnswer => ({ status, body });
const fail = (status: number, code: string): StoreAnswer => ({ status, body: { detail: { code, detail: null } } });

/** Answers a `StoreAnswer` on a route; a 204 carries no body. */
export async function fulfillAnswer(route: Route, out: StoreAnswer) {
  if (out.status === 204) {
    await route.fulfill({ status: 204 });
    return;
  }
  await route.fulfill({ status: out.status, contentType: "application/json", body: JSON.stringify(out.body) });
}

/** Parsed JSON body of a write request, or undefined. */
export function bodyOf(req: { method(): string; postDataJSON(): unknown }): unknown {
  if (req.method() === "GET" || req.method() === "DELETE") return undefined;
  try {
    return req.postDataJSON();
  } catch {
    return undefined;
  }
}

const AGENTS = [
  { id: 1, login: "agent", full_name: "Ada Agent" },
  { id: 2, login: "emuster", full_name: "Erika Muster" },
  { id: 3, login: "mbeispiel", full_name: "Max Beispiel" },
];

const NOW = "2026-10-28T09:00:00Z";
const ME = "Ada Agent";

const fromDemo = <T>(path: string, search = ""): T => structuredClone(resolveApprovalsData(path, "GET", search) as T);

type StepIn = { name?: string; stage?: number; rule?: string; quorum?: number | null; approvers?: unknown[] };

class ApprovalsStore {
  details = new Map<number, ApprovalDetail>();
  /** approval id to the decision of the signed-in agent (inbox tab "Entschieden"). */
  myDecisions = new Map<number, string>();
  nextApprovalId = 510;
  linkUsed = false;
  portalDecision: "approved" | "rejected" | null = null;
  templates: ApprovalTemplate[] = fromDemo<ApprovalTemplate[]>("/api/v1/admin/approvals/templates");
  nextTemplateId = 100;
  delegations: ApprovalDelegation[] = fromDemo<ApprovalDelegation[]>("/api/v1/approvals/delegations");
  nextDelegationId = 100;
  /** A delegation that was ended elsewhere: ending it again answers 403 `delegation_not_active`. */
  delegationEndedElsewhere = false;

  constructor() {
    for (const id of [501, 502, 503]) this.details.set(id, fromDemo<ApprovalDetail>(`/api/v1/approvals/${id}`));
  }

  /** Test hook: the substitution ended meanwhile (another tab, the end date passed). */
  endDelegationElsewhere() {
    this.delegationEndedElsewhere = true;
  }

  private openCount() {
    return [...this.details.values()].filter((d) => d.status === "pending" && d.my_rows.length > 0).length;
  }

  private inbox(state: string | null) {
    const all = [...this.details.values()];
    const wanted =
      state === "decided"
        ? all.filter((d) => d.status !== "pending" || this.myDecisions.has(d.id))
        : all.filter((d) => d.status === "pending" && d.my_rows.length > 0);
    const items = wanted.map((d) => ({ ...inboxItemOf(d), my_decision: this.myDecisions.get(d.id) ?? inboxItemOf(d).my_decision }));
    return { items, total: items.length, limit: 25, offset: 0 };
  }

  private finish(d: ApprovalDetail, status: "approved" | "rejected" | "cancelled") {
    d.status = status;
    d.current_stage = null;
    d.decided_at = NOW;
    d.can_cancel = false;
    d.can_refresh = false;
    d.my_rows = [];
    for (const s of d.steps) {
      if (s.status === "active" || s.status === "waiting") s.status = "not_needed";
      for (const a of s.approvers) if (a.status === "pending" || a.status === "waiting") a.status = "not_needed";
    }
    d.events.push({ at: NOW, action: status === "cancelled" ? "cancelled" : "decided", actor_label: status === "cancelled" ? ME : "System", channel: null, detail: { status } });
  }

  private vote(d: ApprovalDetail, approverId: number | null, decision: "approved" | "rejected", comment: string | null) {
    const row = d.my_rows.find((r) => r.approver_id === approverId) ?? d.my_rows[0];
    if (d.status !== "pending") return fail(409, "approval_closed");
    if (!row) return fail(403, "not_an_approver");
    if (decision === "rejected" && !comment) return fail(422, "invalid_definition");
    const step = d.steps.find((s) => s.id === row.step_id)!;
    const approver = step.approvers.find((a) => a.id === row.approver_id)!;
    approver.status = decision;
    approver.vote = { decision, comment, decided_by_label: ME, on_behalf: false, channel: "web", decided_at: NOW };
    d.my_rows = d.my_rows.filter((r) => r.approver_id !== row.approver_id);
    this.myDecisions.set(d.id, decision);
    d.events.push({ at: NOW, action: "voted", actor_label: ME, channel: "web", detail: { decision } });
    if (decision === "rejected") {
      step.status = "rejected";
      this.finish(d, "rejected");
      return ok(d);
    }
    step.approved_count += 1;
    if (step.approved_count < step.required) return ok(d);
    step.status = "approved";
    for (const a of step.approvers) if (a.status === "pending") a.status = "not_needed";
    const next = d.steps.filter((s) => s.status === "waiting").sort((a, b) => a.stage - b.stage)[0];
    if (!next) {
      this.finish(d, "approved");
      return ok(d);
    }
    for (const s of d.steps.filter((x) => x.stage === next.stage && x.status === "waiting")) {
      s.status = "active";
      for (const a of s.approvers) a.status = "pending";
    }
    d.current_stage = next.stage;
    return ok(d);
  }

  private request(ticketId: number, body: { title?: string; description?: string | null; template_key?: string | null; definition?: { steps?: StepIn[] } | null }) {
    const id = this.nextApprovalId++;
    let seq = 8000 + id * 10;
    const specs: StepIn[] =
      body.definition?.steps ??
      (body.template_key === "cab_normal"
        ? [
            { name: "CAB", stage: 1, rule: "majority", approvers: [{}, {}, {}] },
            { name: "Betriebsleitung", stage: 2, rule: "any", approvers: [{}] },
          ]
        : [{ name: "Freigabe", stage: 1, rule: "any", approvers: [{}] }]);
    const first = Math.min(...specs.map((s) => s.stage ?? 1));
    const steps = specs.map((s, i) => {
      const active = (s.stage ?? 1) === first;
      const people = s.approvers?.length ? s.approvers : [{}];
      const required = s.rule === "all" ? people.length : s.rule === "majority" ? Math.floor(people.length / 2) + 1 : s.rule === "quorum" ? (s.quorum ?? 1) : 1;
      return {
        id: seq++,
        position: i + 1,
        stage: s.stage ?? 1,
        name: s.name || `Schritt ${i + 1}`,
        rule: s.rule ?? "any",
        quorum: s.quorum ?? null,
        required,
        approved_count: 0,
        status: active ? "active" : "waiting",
        reason: null,
        due_at: null,
        overdue: false,
        approvers: people.map((_, k) => ({
          id: seq++,
          kind: "agent",
          display_name: AGENTS[1 + (k % 2)].full_name,
          source_kind: "user",
          source_label: null,
          decisive: false,
          status: active ? "pending" : "waiting",
          vote: null,
        })),
      };
    });
    const detail: ApprovalDetail = {
      id,
      ticket_id: ticketId,
      ticket_number: `20261028${ticketId}`,
      ticket_title: "Ticket",
      ticket_link: true,
      subject_type: "ticket",
      subject_ref: null,
      approval_key: "default",
      title: body.title ?? "",
      description: body.description ?? null,
      describe_lines: [],
      status: "pending",
      reason: null,
      current_stage: first,
      requester_label: ME,
      created_at: NOW,
      decided_at: null,
      can_cancel: true,
      can_refresh: true,
      my_rows: [],
      steps,
      events: [{ at: NOW, action: "requested", actor_label: ME, channel: "web", detail: null }],
    } as ApprovalDetail;
    this.details.set(id, detail);
    return ok(detail, 201);
  }

  private portalItem(): PortalApprovalItem {
    const item = fromDemo<{ items: PortalApprovalItem[] }>("/api/portal/approvals").items[0];
    const d = this.portalDecision;
    return d ? { ...item, status: d, my_status: d, my_decision: d, can_decide: false, decided_at: NOW } : item;
  }

  private portal(path: string, method: string, search: string, body: { decision?: string } | undefined): StoreAnswer | undefined {
    if (path.endsWith("/api/portal/approvals") && method === "GET") {
      const decided = new URLSearchParams(search).get("state") === "decided";
      const item = this.portalItem();
      const items = decided === (this.portalDecision !== null) ? [item] : [];
      return ok({ items, total: items.length, limit: 25, offset: 0 });
    }
    const m = path.match(/\/api\/portal\/approvals\/601(\/decision)?$/);
    if (!m) return undefined;
    if (m[1] && method === "POST") {
      if (this.portalDecision) return fail(409, "already_decided");
      this.portalDecision = body?.decision === "rejected" ? "rejected" : "approved";
    }
    const detail = fromDemo<PortalApproval>("/api/portal/approvals/601");
    const d = this.portalDecision;
    return ok(d ? { ...detail, status: d, my_status: d, my_decision: d, can_decide: false } : detail);
  }

  private link(path: string, method: string, body: { token?: string; decision?: string } | undefined): StoreAnswer | undefined {
    if (method !== "POST") return undefined;
    const live = body?.token === DEMO_APPROVAL_LINK_TOKEN;
    if (path.endsWith("/approval-links/inspect") && live && this.linkUsed) return ok({ valid: false, code: "link_used" });
    if (path.endsWith("/approval-links/decide") && live && this.linkUsed) return fail(410, "link_used");
    if (path.endsWith("/approval-links/decide") && live) this.linkUsed = true;
    return resolveApprovalLink(path, method, body);
  }

  /** Answers like the demo resolver, with state; `undefined` for paths that are not approvals. */
  handle(path: string, method: string, search: string, body: unknown): StoreAnswer | undefined {
    const b = body as Record<string, unknown> | undefined;
    if (path.includes("/api/portal/approvals")) return this.portal(path, method, search, b as { decision?: string } | undefined);
    if (path.includes("/api/v1/approval-links/")) return this.link(path, method, b as { token?: string; decision?: string } | undefined);
    if (path.endsWith("/api/v1/reference/agents") && method === "GET") return ok(AGENTS);

    // Agent plane
    if (path.endsWith("/api/v1/approvals/inbox") && method === "GET") return ok(this.inbox(new URLSearchParams(search).get("state")));
    if (path.endsWith("/api/v1/approvals/inbox/count") && method === "GET") return ok({ pending: this.openCount() });
    const one = path.match(/\/api\/v1\/approvals\/(\d+)(?:\/(decision|cancel))?$/);
    if (one) {
      const d = this.details.get(Number(one[1]));
      if (!d) return fail(404, "approval_not_found");
      if (one[2] === "decision" && method === "POST") {
        return this.vote(d, (b?.approver_id as number | null) ?? null, b?.decision === "rejected" ? "rejected" : "approved", (b?.comment as string | null) ?? null);
      }
      if (one[2] === "cancel" && method === "POST") {
        if (d.status !== "pending") return fail(409, "approval_closed");
        this.finish(d, "cancelled");
        return ok(d);
      }
      if (!one[2] && method === "GET") return ok(d);
    }
    const onTicket = path.match(/\/api\/v1\/tickets\/(\d+)\/approvals$/);
    if (onTicket) {
      const ticketId = Number(onTicket[1]);
      if (method === "GET") return ok([...this.details.values()].filter((d) => d.ticket_id === ticketId).map(summaryOf));
      if (method === "POST") return this.request(ticketId, (b ?? {}) as Parameters<ApprovalsStore["request"]>[1]);
    }
    if (path.endsWith("/api/v1/approvals/delegations")) {
      if (method === "GET") return ok(this.delegations);
      if (method === "POST") {
        const until = b?.valid_until as string | null | undefined;
        if (until && new Date(until).getTime() <= Date.now()) return fail(422, "invalid_definition");
        const sub = AGENTS.find((a) => a.id === b?.substitute_user_id);
        if (!sub) return fail(422, "invalid_member");
        const row: ApprovalDelegation = {
          id: this.nextDelegationId++,
          user_id: 1,
          user_label: ME,
          substitute_user_id: sub.id,
          substitute_label: sub.full_name,
          valid_from: (b?.valid_from as string | null) ?? NOW,
          valid_until: until ?? null,
          created_at: NOW,
        };
        this.delegations.push(row);
        return ok(row, 201);
      }
    }
    const delegation = path.match(/\/api\/v1\/approvals\/delegations\/(\d+)$/);
    if (delegation && method === "DELETE") {
      if (this.delegationEndedElsewhere) return fail(403, "delegation_not_active");
      this.delegations = this.delegations.filter((x) => x.id !== Number(delegation[1]));
      return { status: 204, body: null };
    }

    // Admin plane
    if (path.endsWith("/api/v1/admin/approvals/templates")) {
      if (method === "GET") return ok(this.templates);
      if (method === "POST") {
        if (this.templates.some((x) => x.template_key === b?.template_key)) return fail(409, "template_key_taken");
        const row = { id: this.nextTemplateId++, origin: "admin", warnings: [], valid: true, ...b, change_time: new Date().toISOString() } as unknown as ApprovalTemplate;
        this.templates.push(row);
        return ok(row, 201);
      }
    }
    const tpl = path.match(/\/api\/v1\/admin\/approvals\/templates\/(\d+)$/);
    if (tpl && method === "DELETE") {
      this.templates = this.templates.filter((x) => x.id !== Number(tpl[1]));
      return { status: 204, body: null };
    }

    const out = resolveApprovalsData(path, method, search);
    if (out === undefined) return undefined;
    const created = method === "POST" && /\/(approvals|admin\/approvals\/(lists|delegations))$/.test(path);
    return ok(out, created ? 201 : 200);
  }
}

let current: { info: unknown; store: ApprovalsStore } | null = null;

/** The approvals store of the running test attempt (a retry starts from the demo records again). */
export function approvalsStore(): ApprovalsStore {
  const info = test.info();
  if (!current || current.info !== info) current = { info, store: new ApprovalsStore() };
  return current.store;
}
