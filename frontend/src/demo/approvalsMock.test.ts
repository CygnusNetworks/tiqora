import { describe, expect, it } from "vitest";
import { resolveData } from "./mockData";
import { demoApprovalInbox, DEMO_APPROVAL_CHANGE_TICKET_ID, resolveApprovalsData } from "./approvalsMock";

const AGENT_GETS = [
  "/api/v1/approvals/inbox",
  "/api/v1/approvals/inbox/count",
  "/api/v1/approvals/501",
  "/api/v1/approvals/502",
  "/api/v1/approvals/503",
  "/api/v1/approvals/templates",
  "/api/v1/approvals/lists",
  "/api/v1/approvals/resolvers",
  "/api/v1/approvals/delegations",
  `/api/v1/tickets/${DEMO_APPROVAL_CHANGE_TICKET_ID}/approvals`,
];
const PORTAL_GETS = ["/api/portal/approvals", "/api/portal/approvals/601"];
const ADMIN_GETS = [
  "/api/v1/admin/approvals",
  "/api/v1/admin/approvals/subject-types",
  "/api/v1/admin/approvals/templates",
  "/api/v1/admin/approvals/templates/1",
  "/api/v1/admin/approvals/lists",
  "/api/v1/admin/approvals/lists/1",
  "/api/v1/admin/approvals/delegations",
];

describe("demo approvals", () => {
  it.each([...AGENT_GETS, ...PORTAL_GETS, ...ADMIN_GETS])("answers GET %s", (path) => {
    expect(resolveApprovalsData(path, "GET")).not.toBeUndefined();
  });

  it("lists example roles for the approver picker, sorted by name", () => {
    const roles = resolveApprovalsData("/api/v1/reference/roles", "GET") as { id: number; name: string }[];
    expect(roles.map((r) => r.name)).toEqual(["Change-Manager", "Netzwerk", "Service-Desk"]);
    expect(resolveApprovalsData("/api/v1/reference/roles", "POST")).toBeUndefined();
    expect(resolveData("/api/v1/reference/roles", "GET")).toEqual(roles);
  });

  it("answers the writes the UI sends", () => {
    for (const [path, method] of [
      ["/api/v1/approvals/501/decision", "POST"],
      ["/api/v1/approvals/501/approvers/9002/forward", "POST"],
      ["/api/v1/approvals/501/cancel", "POST"],
      ["/api/v1/approvals/501/refresh-approvers", "POST"],
      ["/api/v1/approvals/delegations", "POST"],
      [`/api/v1/tickets/${DEMO_APPROVAL_CHANGE_TICKET_ID}/approvals`, "POST"],
      ["/api/v1/approval-links/inspect", "POST"],
      ["/api/v1/approval-links/decide", "POST"],
      ["/api/portal/approvals/601/decision", "POST"],
      ["/api/v1/admin/approvals/templates/validate", "POST"],
    ] as const) {
      expect(resolveApprovalsData(path, method), `${method} ${path}`).not.toBeUndefined();
    }
  });

  it("ignores foreign paths and unknown ids", () => {
    expect(resolveApprovalsData("/api/v1/tickets", "GET")).toBeUndefined();
    expect(resolveApprovalsData("/api/v1/approvals/999", "GET")).toBeUndefined();
    expect(resolveApprovalsData("/api/v1/approvals/501", "DELETE")).toBeUndefined();
  });

  it("is reachable through the shared demo resolver", () => {
    expect(resolveData("/api/v1/approvals/inbox/count", "GET")).toEqual({ pending: 2 });
  });

  it("splits the inbox by state and counts the open ones", () => {
    expect(demoApprovalInbox("pending").items.map((i) => i.approval_id)).toEqual([501, 503]);
    expect(demoApprovalInbox("decided").items.map((i) => i.approval_id)).toEqual([502]);
    expect(resolveApprovalsData("/api/v1/approvals/inbox", "GET", "?state=decided")).toMatchObject({ total: 1 });
  });

  it("carries the CAB change approval other demo data refer to", () => {
    const d = resolveApprovalsData("/api/v1/approvals/501", "GET") as {
      subject_type: string;
      approval_key: string;
      subject_ref: string;
      ticket_id: number;
      ticket_title: string;
      steps: { stage: number; rule: string; required: number; approvers: unknown[] }[];
      my_rows: unknown[];
    };
    expect([d.subject_type, d.approval_key, d.subject_ref, d.ticket_id]).toEqual(["itsm.change", "change_cab", "1", 130]);
    expect(d.ticket_title).toBe("Switch-Firmware im Rechenzentrum aktualisieren");
    expect(d.steps.map((s) => [s.stage, s.rule])).toEqual([[1, "majority"], [2, "any"]]);
    expect(d.steps[0].approvers).toHaveLength(3);
    expect(d.steps[0].required).toBe(2);
    expect(d.my_rows).toHaveLength(1);
  });

  it("shows the ticket panel only the approvals of that ticket", () => {
    expect(resolveApprovalsData("/api/v1/tickets/130/approvals", "GET")).toHaveLength(1);
    expect(resolveApprovalsData("/api/v1/tickets/100/approvals", "GET")).toMatchObject([{ id: 502, status: "approved" }]);
    expect(resolveApprovalsData("/api/v1/tickets/1/approvals", "GET")).toEqual([]);
  });

  it("has an overdue escalated step, one delegation, two templates and one list", () => {
    const overdue = resolveApprovalsData("/api/v1/approvals/503", "GET") as {
      steps: { overdue: boolean; approvers: { source_kind: string }[] }[];
    };
    expect(overdue.steps[0].overdue).toBe(true);
    expect(overdue.steps[0].approvers.map((a) => a.source_kind)).toContain("escalation");
    expect(resolveApprovalsData("/api/v1/approvals/delegations", "GET")).toHaveLength(1);
    expect(resolveApprovalsData("/api/v1/approvals/templates", "GET")).toHaveLength(2);
    expect(resolveApprovalsData("/api/v1/approvals/lists", "GET")).toHaveLength(1);
  });

  it("uses only example data", () => {
    const all = JSON.stringify([...AGENT_GETS, ...PORTAL_GETS, ...ADMIN_GETS].map((p) => resolveApprovalsData(p, "GET")));
    expect(all).toContain("kunde1@example.org");
    expect(all).not.toMatch(/@(?!example\.org)[a-z0-9.-]+\.(com|de|net)/);
  });
});
