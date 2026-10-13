/** Shared fixtures of the zoom ITSM tests. */
import type { CiSummary, TicketItsm } from "@/lib/itsmApi";
import type { TicketDetail } from "@/lib/api";

export const PERMS = { ro: true, rw: true, note: true, owner: true, priority: true, move_into: true, create: true };

export function makeTicket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: 7,
    tn: "20260701000007",
    title: "Website nicht erreichbar",
    priority_id: 3,
    can_write: true,
    permissions: PERMS,
    dynamic_fields: [],
    ...overrides,
  } as TicketDetail;
}

export function makeCi(id: number, over: Partial<CiSummary> = {}): CiSummary {
  return {
    id,
    ref: `znuny:${id}`,
    source: "znuny",
    number: `100200${id}`,
    name: `srv-${id}.example.org`,
    class_key: "computer",
    class_name: "Computer",
    deployment_state: { key: "1", name: "Produktiv", functionality: "operational" },
    incident_state: { key: "2", name: "Störung", functionality: "incident" },
    changed_at: null,
    edit_url: null,
    ...over,
  };
}

export function makeItsm(over: Partial<TicketItsm> = {}): TicketItsm {
  return {
    enabled: true,
    mode: "znuny_readthrough",
    role: null,
    criticality: null,
    impact: null,
    matrix_priority_id: null,
    service: null,
    major_incident: null,
    major_banner: null,
    problem: null,
    ci_count: 0,
    recent_incident: null,
    dialogs: { itsm_fields: false, decision: false },
    known_error_category_configured: true,
    major_incident_customer_reply: false,
    ...over,
  };
}
