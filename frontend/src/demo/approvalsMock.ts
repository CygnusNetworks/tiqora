/**
 * Demo / screenshot / e2e data for approvals (agent inbox + detail, ticket panel, portal, public
 * link, admin pages). Path-scoped like `itsmMock.ts`: answers only its own paths and returns
 * `undefined` for everything else, so `resolveData` falls through. The resolver sees the path (and
 * optionally the query string) but no request body, so writes answer a fixed record.
 *
 * Records: a CAB change approval (ticket 130, two stages: majority of three, then any — the
 * logged-in demo agent still has to vote), an ad-hoc ticket approval that is decided, a request
 * with an overdue, escalated step, one delegation, two templates and one Freigabegruppe.
 * The CAB entry uses `subject_type "itsm.change"`, `approval_key "change_cab"`, `subject_ref "1"`
 * and ticket 130; the ITSM change demo data reuses that ticket id.
 *
 * All example data uses example.org / .test only.
 */
import type {
  ApprovalApprover,
  ApprovalDelegation,
  ApprovalDetail,
  ApprovalInboxItem,
  ApprovalInboxPage,
  ApprovalListOption,
  ApprovalResolverOption,
  ApprovalSummary,
  ApprovalTemplateOption,
} from "@/lib/approvalsApi";
import type { ApprovalAdminItem, ApprovalList, ApprovalTemplate } from "@/lib/approvalsAdminApi";
import type { PortalApproval, PortalApprovalItem } from "@/lib/portalApprovalsApi";
import type { ApprovalLinkInspect } from "@/lib/approvalLinkApi";

const T0 = "2026-10-12T08:00:00Z";
const T1 = "2026-10-12T09:30:00Z";
const T2 = "2026-10-13T07:45:00Z";
/** Far enough in the future / far enough in the past for "überfällig" to be stable. */
const DUE_FUTURE = "2030-01-15T12:00:00Z";
const DUE_PAST = "2026-10-10T12:00:00Z";

/** Ticket the CAB change approval hangs on (ITSM change demo data reuses this id). */
export const DEMO_APPROVAL_CHANGE_TICKET_ID = 130;

const approver = (
  id: number,
  display_name: string,
  status: string,
  over: Partial<ApprovalApprover> = {},
): ApprovalApprover => ({
  id,
  kind: "agent",
  display_name,
  source_kind: "user",
  source_label: null,
  decisive: false,
  status,
  vote: null,
  ...over,
});

const approvedVote = (who: string, comment: string | null, at: string) => ({
  decision: "approved",
  comment,
  decided_by_label: who,
  on_behalf: false,
  channel: "web",
  decided_at: at,
});

const cabChange: ApprovalDetail = {
  id: 501,
  ticket_id: DEMO_APPROVAL_CHANGE_TICKET_ID,
  ticket_number: "20261012130",
  ticket_title: "Switch-Firmware im Rechenzentrum aktualisieren",
  ticket_link: true,
  subject_type: "itsm.change",
  subject_ref: "1",
  approval_key: "change_cab",
  title: "Change freigeben: Switch-Firmware im Rechenzentrum aktualisieren",
  description: "Wartungsfenster Samstag 22:00 bis 02:00 Uhr, Rückfall auf die vorherige Firmware ist getestet.",
  describe_lines: ["Change-Typ: Normal", "Risiko: mittel", "Betroffene Systeme: 2 Core-Switches"],
  status: "pending",
  reason: null,
  current_stage: 1,
  requester_label: "Max Beispiel",
  created_at: T0,
  decided_at: null,
  can_cancel: true,
  can_refresh: true,
  my_rows: [{ approver_id: 9002, step_id: 7001, on_behalf_of_label: null, can_forward: true }],
  steps: [
    {
      id: 7001,
      position: 1,
      stage: 1,
      name: "CAB",
      rule: "majority",
      quorum: null,
      required: 2,
      approved_count: 1,
      status: "active",
      reason: null,
      due_at: DUE_FUTURE,
      overdue: false,
      approvers: [
        approver(9001, "Erika Muster", "approved", {
          source_kind: "list",
          source_label: "über CAB Netzwerk",
          vote: approvedVote("Erika Muster", "Rückfallplan ist ausreichend.", T1),
        }),
        approver(9002, "Alex Turner", "pending", { source_kind: "list", source_label: "über CAB Netzwerk" }),
        approver(9003, "Max Beispiel", "pending", { source_kind: "list", source_label: "über CAB Netzwerk" }),
      ],
    },
    {
      id: 7002,
      position: 2,
      stage: 2,
      name: "Betriebsleitung",
      rule: "any",
      quorum: null,
      required: 1,
      approved_count: 0,
      status: "waiting",
      reason: null,
      due_at: null,
      overdue: false,
      approvers: [approver(9004, "Erika Muster", "waiting", { source_kind: "group", source_label: "Gruppe Betriebsleitung" })],
    },
  ],
  events: [
    { at: T0, action: "requested", actor_label: "Max Beispiel", channel: "web", detail: null },
    { at: T0, action: "notified", actor_label: "System", channel: null, detail: null },
    { at: T1, action: "voted", actor_label: "Erika Muster", channel: "web", detail: { decision: "approved" } },
  ],
};

const adHocDecided: ApprovalDetail = {
  id: 502,
  ticket_id: 100,
  ticket_number: "20261012100",
  ticket_title: "Zugang zum Archivserver einrichten",
  ticket_link: true,
  subject_type: "ticket",
  subject_ref: null,
  approval_key: "default",
  title: "Zugang zum Archivserver freigeben",
  description: null,
  describe_lines: [],
  status: "approved",
  reason: null,
  current_stage: null,
  requester_label: "Max Beispiel",
  created_at: T0,
  decided_at: T1,
  can_cancel: false,
  can_refresh: false,
  my_rows: [],
  steps: [
    {
      id: 7003,
      position: 1,
      stage: 1,
      name: "Freigabe",
      rule: "any",
      quorum: null,
      required: 1,
      approved_count: 1,
      status: "approved",
      reason: null,
      due_at: null,
      overdue: false,
      approvers: [
        approver(9005, "Erika Muster", "approved", { vote: approvedVote("Erika Muster", "Passt, bitte umsetzen.", T1) }),
      ],
    },
  ],
  events: [
    { at: T0, action: "requested", actor_label: "Max Beispiel", channel: "web", detail: null },
    { at: T1, action: "voted", actor_label: "Erika Muster", channel: "web", detail: { decision: "approved" } },
    { at: T1, action: "decided", actor_label: "System", channel: null, detail: { status: "approved" } },
  ],
};

const overdueEscalated: ApprovalDetail = {
  id: 503,
  ticket_id: 131,
  ticket_number: "20261012131",
  ticket_title: "Firewall-Regel für Lieferanten-VPN öffnen",
  ticket_link: true,
  subject_type: "ticket",
  subject_ref: null,
  approval_key: "default",
  title: "Firewall-Regel freigeben",
  description: "Port 443 zu 192.0.2.10 für den Lieferanten-Zugang.",
  describe_lines: [],
  status: "pending",
  reason: null,
  current_stage: 1,
  requester_label: "Max Beispiel",
  created_at: T0,
  decided_at: null,
  can_cancel: true,
  can_refresh: true,
  my_rows: [{ approver_id: 9007, step_id: 7004, on_behalf_of_label: null, can_forward: true }],
  steps: [
    {
      id: 7004,
      position: 1,
      stage: 1,
      name: "Sicherheit",
      rule: "any",
      quorum: null,
      required: 1,
      approved_count: 0,
      status: "active",
      reason: null,
      due_at: DUE_PAST,
      overdue: true,
      approvers: [
        approver(9006, "Erika Muster", "pending"),
        approver(9007, "Alex Turner", "pending", { decisive: true, source_kind: "escalation", source_label: "Eskalation" }),
      ],
    },
  ],
  events: [
    { at: T0, action: "requested", actor_label: "Max Beispiel", channel: "web", detail: null },
    { at: T2, action: "overdue", actor_label: "System", channel: null, detail: null },
    { at: T2, action: "escalated", actor_label: "System", channel: null, detail: null },
  ],
};

const DETAILS: Record<number, ApprovalDetail> = { 501: cabChange, 502: adHocDecided, 503: overdueEscalated };

const summaryOf = (d: ApprovalDetail): ApprovalSummary => ({
  id: d.id,
  ticket_id: d.ticket_id,
  subject_type: d.subject_type,
  subject_ref: d.subject_ref,
  approval_key: d.approval_key,
  title: d.title,
  status: d.status,
  reason: d.reason,
  current_stage: d.current_stage,
  requester_label: d.requester_label,
  created_at: d.created_at,
  decided_at: d.decided_at,
  can_cancel: d.can_cancel,
  my_pending: d.my_rows.length > 0 && d.status === "pending",
});

const inboxItemOf = (d: ApprovalDetail): ApprovalInboxItem => {
  const step = d.steps.find((s) => s.status === "active") ?? d.steps[d.steps.length - 1];
  const row = d.my_rows[0];
  return {
    approval_id: d.id,
    approver_id: row?.approver_id ?? step.approvers[0].id,
    ticket_id: d.ticket_id,
    ticket_number: d.ticket_number,
    ticket_title: d.ticket_title,
    subject_type: d.subject_type,
    title: d.title,
    step_name: step.name,
    rule: step.rule,
    required: step.required,
    approved_count: step.approved_count,
    status: d.status,
    due_at: step.due_at,
    overdue: step.overdue,
    can_vote: d.status === "pending" && !!row,
    blocked_reason: null,
    on_behalf_of_label: row?.on_behalf_of_label ?? null,
    my_decision: d.status === "approved" ? "approved" : null,
    decided_at: d.decided_at,
  };
};

/** `?state=` of the inbox: "decided" lists the finished ones, everything else the open ones. */
export function demoApprovalInbox(state: string | null): ApprovalInboxPage {
  const wanted = state === "decided" ? [502] : [501, 503];
  const items = wanted.map((id) => inboxItemOf(DETAILS[id]));
  return { items, total: items.length, limit: 25, offset: 0 };
}

const TEMPLATES: ApprovalTemplateOption[] = [
  { key: "standard_einzel", name: "Einzelfreigabe", description: "Eine Person genügt.", subject_types: ["ticket"] },
  { key: "cab_normal", name: "CAB (Mehrheit)", description: "Mehrheit der Freigabegruppe, danach Betriebsleitung.", subject_types: [] },
];

const LISTS: ApprovalListOption[] = [{ id: 1, name: "CAB Netzwerk", member_count: 3 }];

const RESOLVERS: ApprovalResolverOption[] = [
  { key: "ticket.customer_user", title_key: "approvals.resolver.ticket_customer_user", yields_customers: true },
  { key: "ticket.owner", title_key: "approvals.resolver.ticket_owner", yields_customers: false },
  { key: "ticket.responsible", title_key: "approvals.resolver.ticket_responsible", yields_customers: false },
];

/** Roles for the approver picker (`GET /reference/roles`). */
const DEMO_ROLES = [
  { id: 1, name: "Change-Manager" },
  { id: 2, name: "Netzwerk" },
  { id: 3, name: "Service-Desk" },
];

const DELEGATION: ApprovalDelegation = {
  id: 1,
  user_id: 1,
  user_label: "Alex Turner",
  substitute_user_id: 2,
  substitute_label: "Erika Muster",
  valid_from: "2026-10-20T00:00:00Z",
  valid_until: "2026-11-03T00:00:00Z",
  created_at: T0,
};

const ADMIN_ITEMS: ApprovalAdminItem[] = [cabChange, adHocDecided, overdueEscalated].map((d) => {
  const step = d.steps.find((s) => s.status === "active") ?? d.steps[d.steps.length - 1];
  return {
    id: d.id,
    ticket_id: d.ticket_id,
    ticket_number: d.ticket_number,
    ticket_title: d.ticket_title,
    subject_type: d.subject_type,
    subject_ref: d.subject_ref,
    approval_key: d.approval_key,
    title: d.title,
    status: d.status,
    reason: d.reason,
    current_stage: d.current_stage,
    requester_label: d.requester_label,
    created_at: d.created_at,
    decided_at: d.decided_at,
    step_name: step.name,
    open_approvers: step.approvers.filter((a) => a.status === "pending").length,
    overdue: d.steps.some((s) => s.overdue),
    can_cancel: d.can_cancel,
  };
});

const ADMIN_TEMPLATES: ApprovalTemplate[] = [
  {
    id: 1,
    template_key: "standard_einzel",
    name: "Einzelfreigabe",
    description: "Eine Person genügt.",
    subject_types: ["ticket"],
    origin: "admin",
    valid: true,
    warnings: [],
    definition: {
      steps: [{ name: "Freigabe", stage: 1, rule: "any", approvers: [{ kind: "resolver", ref: "ticket.owner" }], on_timeout: "notify", on_no_approver: "reject", allow_delegation: true, allow_email: true, escalation: [] }],
    },
  },
  {
    id: 2,
    template_key: "cab_normal",
    name: "CAB (Mehrheit)",
    description: "Mehrheit der Freigabegruppe, danach Betriebsleitung.",
    subject_types: [],
    origin: "admin",
    valid: true,
    warnings: [],
    definition: {
      steps: [
        { name: "CAB", stage: 1, rule: "majority", approvers: [{ kind: "list", ref: "1" }], due_hours: 72, on_timeout: "escalate", on_no_approver: "reject", allow_delegation: true, allow_email: true, escalation: [{ kind: "group", ref: "admin" }] },
        { name: "Betriebsleitung", stage: 2, rule: "any", approvers: [{ kind: "group", ref: "Betriebsleitung" }], on_timeout: "notify", on_no_approver: "reject", allow_delegation: true, allow_email: true, escalation: [] },
      ],
    },
  },
];

const ADMIN_LISTS: ApprovalList[] = [
  {
    id: 1,
    name: "CAB Netzwerk",
    description: "Change-Advisory-Board für Netzwerkänderungen.",
    valid: true,
    member_ids: [1, 2, 3],
    members: [
      { id: 1, label: "Alex Turner" },
      { id: 2, label: "Erika Muster" },
      { id: 3, label: "Max Beispiel" },
    ],
  },
];

const PORTAL_ITEM: PortalApprovalItem = {
  id: 601,
  title: "Neuen Zugang für den Dienstleister freigeben",
  ticket_number: "20261012140",
  ticket_title: "Dienstleister-Zugang beantragt",
  step_name: "Kundenfreigabe",
  status: "pending",
  due_at: DUE_FUTURE,
  my_status: "pending",
  my_decision: null,
  can_decide: true,
  decided_at: null,
};

const PORTAL_DETAIL: PortalApproval = {
  id: 601,
  title: PORTAL_ITEM.title,
  description: "Bitte bestätigen Sie den Zugang für kunde1@example.org.",
  describe_lines: ["Beantragt für: kunde1@example.org", "Laufzeit: 3 Monate"],
  ticket_number: PORTAL_ITEM.ticket_number,
  ticket_title: PORTAL_ITEM.ticket_title,
  status: "pending",
  step_name: "Kundenfreigabe",
  rule_text_key: "approvals.rule.any",
  required: 1,
  approved_count: 0,
  due_at: DUE_FUTURE,
  my_status: "pending",
  my_decision: null,
  can_decide: true,
};

const LINK_INSPECT: ApprovalLinkInspect = {
  valid: true,
  code: null,
  title: cabChange.title,
  ticket_number: cabChange.ticket_number,
  ticket_title: cabChange.ticket_title,
  step_name: "CAB",
  rule: "majority",
  required: 2,
  approver_name: "Erika Muster",
  due_at: DUE_FUTURE,
  description: cabChange.description,
  describe_lines: cabChange.describe_lines,
};

/** Only the token `demo` is a live link; everything else is a dead one (`link_invalid`). */
export const DEMO_APPROVAL_LINK_TOKEN = "demo";

/**
 * Public link endpoints with the request body (the plain resolver never sees it): inspect and
 * decide answer for the demo token, decide echoes the chosen decision as the request status.
 */
export function resolveApprovalLink(
  path: string,
  method: string,
  body: unknown,
): { status: number; body: unknown } | undefined {
  if (method !== "POST") return undefined;
  const isInspect = path.endsWith("/api/v1/approval-links/inspect");
  const isDecide = path.endsWith("/api/v1/approval-links/decide");
  if (!isInspect && !isDecide) return undefined;
  const sent = (body ?? {}) as { token?: unknown; decision?: unknown };
  const live = sent.token === DEMO_APPROVAL_LINK_TOKEN;
  if (isInspect) return { status: 200, body: live ? LINK_INSPECT : { valid: false, code: "link_invalid" } };
  if (!live) return { status: 410, body: { detail: { code: "link_invalid", detail: null } } };
  const decision = sent.decision === "rejected" ? "rejected" : "approved";
  return { status: 200, body: { status: decision, decision } };
}

const idOf = (m: RegExpMatchArray | null) => (m ? Number(m[1]) : null);

/** Agent routes `/api/v1/approvals/*`, ticket panel, public link, portal and admin; `undefined` for any other path. */
export function resolveApprovalsData(path: string, method: string, search = ""): unknown | undefined {
  const get = method === "GET";
  // Public link (no auth)
  if (path.endsWith("/api/v1/approval-links/inspect") && method === "POST") return LINK_INSPECT;
  if (path.endsWith("/api/v1/approval-links/decide") && method === "POST") return { status: "approved", decision: "approved" };

  // Ticket panel
  const onTicket = path.match(/\/api\/v1\/tickets\/(\d+)\/approvals$/);
  if (onTicket) {
    if (get) {
      const ticketId = Number(onTicket[1]);
      return Object.values(DETAILS).filter((d) => d.ticket_id === ticketId).map(summaryOf);
    }
    if (method === "POST") return adHocDecided;
  }

  // Portal plane
  if (path.endsWith("/api/portal/approvals") && get) {
    const decided = new URLSearchParams(search).get("state") === "decided";
    const items = decided ? [] : [PORTAL_ITEM];
    return { items, total: items.length, limit: 25, offset: 0 };
  }
  const portalOne = path.match(/\/api\/portal\/approvals\/(\d+)(\/decision)?$/);
  if (portalOne && portalOne[1] === "601" && (get || (method === "POST" && portalOne[2]))) return PORTAL_DETAIL;

  // Admin plane
  if (path.endsWith("/api/v1/admin/approvals") && get) {
    return { items: ADMIN_ITEMS, total: ADMIN_ITEMS.length, limit: 25, offset: 0 };
  }
  if (path.endsWith("/api/v1/admin/approvals/templates") && get) return ADMIN_TEMPLATES;
  if (path.endsWith("/api/v1/admin/approvals/templates") && method === "POST") return ADMIN_TEMPLATES[0];
  if (path.endsWith("/api/v1/admin/approvals/templates/validate") && method === "POST") return { errors: [], warnings: [] };
  const adminTemplate = path.match(/\/api\/v1\/admin\/approvals\/templates\/(\d+)$/);
  if (adminTemplate) {
    const row = ADMIN_TEMPLATES.find((x) => x.id === Number(adminTemplate[1]));
    if (row && (get || method === "PUT")) return row;
  }
  if (path.endsWith("/api/v1/admin/approvals/lists") && get) return ADMIN_LISTS;
  if (path.endsWith("/api/v1/admin/approvals/lists") && method === "POST") return ADMIN_LISTS[0];
  const adminList = path.match(/\/api\/v1\/admin\/approvals\/lists\/(\d+)$/);
  if (adminList && Number(adminList[1]) === 1 && (get || method === "PUT")) return ADMIN_LISTS[0];
  if (path.endsWith("/api/v1/admin/approvals/delegations") && get) return [DELEGATION];
  if (path.endsWith("/api/v1/admin/approvals/delegations") && method === "POST") return DELEGATION;

  // Agent plane
  if (path.endsWith("/api/v1/reference/roles") && get) return DEMO_ROLES;
  if (path.endsWith("/api/v1/approvals/inbox") && get) return demoApprovalInbox(new URLSearchParams(search).get("state"));
  if (path.endsWith("/api/v1/approvals/inbox/count") && get) return { pending: demoApprovalInbox("pending").items.length };
  if (path.endsWith("/api/v1/approvals/templates") && get) return TEMPLATES;
  if (path.endsWith("/api/v1/approvals/lists") && get) return LISTS;
  if (path.endsWith("/api/v1/approvals/resolvers") && get) return RESOLVERS;
  if (path.endsWith("/api/v1/approvals/delegations") && get) return [DELEGATION];
  if (path.endsWith("/api/v1/approvals/delegations") && method === "POST") return DELEGATION;
  const one = path.match(/\/api\/v1\/approvals\/(\d+)(\/(?:decision|cancel|refresh-approvers|approvers\/\d+\/forward))?$/);
  const id = idOf(one);
  if (one && id !== null && DETAILS[id] && (one[2] ? method === "POST" : get)) return DETAILS[id];
  return undefined;
}
