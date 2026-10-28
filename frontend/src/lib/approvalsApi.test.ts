import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import de from "@/i18n/locales/de.json";
import en from "@/i18n/locales/en.json";
import { api, ApiError } from "./api";
import { approvalLinkApi } from "./approvalLinkApi";
import {
  APPROVAL_ERROR_CODES,
  approvalErrorCode,
  approvalErrorMessage,
  approvalRuleKey,
  approvalsApi,
  approvalsKeys,
} from "./approvalsApi";
import { approvalsAdminApi } from "./approvalsAdminApi";
import { portalApprovalsApi } from "./portalApprovalsApi";
import { portalApi } from "./portalApi";

afterEach(() => vi.restoreAllMocks());

type Call = [method: unknown, path: unknown, init: { query?: unknown; body?: unknown } | undefined];
const calls = (spy: { mock: { calls: unknown[][] } }) =>
  spy.mock.calls.map((c) => [c[0], c[1], c[2]] as Call).map(([m, p, i]) => [m, p, i?.query, i?.body]);

const apiError = (status: number, code: string) => new ApiError(status, { detail: { code, detail: null } }, "/x");

describe("approvalsApi paths, queries and bodies", () => {
  it("builds the inbox, detail and vote routes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await approvalsApi.inbox({ state: "decided", limit: 25, offset: 50 });
    await approvalsApi.inboxCount();
    await approvalsApi.get(501);
    await approvalsApi.decide(501, { decision: "rejected", comment: "Rückfallplan fehlt", approver_id: 9002 });
    await approvalsApi.forward(501, 9002, { user_id: 2 });
    await approvalsApi.cancel(501);
    await approvalsApi.refreshApprovers(501);
    expect(calls(spy)).toEqual([
      ["GET", "/api/v1/approvals/inbox", { state: "decided", limit: 25, offset: 50 }, undefined],
      ["GET", "/api/v1/approvals/inbox/count", undefined, undefined],
      ["GET", "/api/v1/approvals/501", undefined, undefined],
      ["POST", "/api/v1/approvals/501/decision", undefined, { decision: "rejected", comment: "Rückfallplan fehlt", approver_id: 9002 }],
      ["POST", "/api/v1/approvals/501/approvers/9002/forward", undefined, { user_id: 2 }],
      ["POST", "/api/v1/approvals/501/cancel", undefined, undefined],
      ["POST", "/api/v1/approvals/501/refresh-approvers", undefined, undefined],
    ]);
  });

  it("builds the lookup, delegation and ticket routes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue([] as never);
    await approvalsApi.templates("ticket");
    await approvalsApi.templates();
    await approvalsApi.lists();
    await approvalsApi.resolvers("itsm.change");
    await approvalsApi.delegations();
    await approvalsApi.createDelegation({ substitute_user_id: 2, valid_from: "2026-10-20T00:00:00Z", valid_until: null });
    await approvalsApi.deleteDelegation(1);
    await approvalsApi.ticketApprovals(130, { limit: 20, offset: 0 });
    await approvalsApi.requestApproval(130, { title: "Zugang freigeben", approval_key: "default", template_key: "standard_einzel" });
    expect(calls(spy)).toEqual([
      ["GET", "/api/v1/approvals/templates", { subject_type: "ticket" }, undefined],
      ["GET", "/api/v1/approvals/templates", { subject_type: undefined }, undefined],
      ["GET", "/api/v1/approvals/lists", undefined, undefined],
      ["GET", "/api/v1/approvals/resolvers", { subject_type: "itsm.change" }, undefined],
      ["GET", "/api/v1/approvals/delegations", undefined, undefined],
      ["POST", "/api/v1/approvals/delegations", undefined, { substitute_user_id: 2, valid_from: "2026-10-20T00:00:00Z", valid_until: null }],
      ["DELETE", "/api/v1/approvals/delegations/1", undefined, undefined],
      ["GET", "/api/v1/tickets/130/approvals", { limit: 20, offset: 0 }, undefined],
      ["POST", "/api/v1/tickets/130/approvals", undefined, { title: "Zugang freigeben", approval_key: "default", template_key: "standard_einzel" }],
    ]);
  });

  it("keeps every query key under the approvals prefix, except the ticket list", () => {
    expect(approvalsKeys.inbox("pending", 0)).toEqual(["approvals", "inbox", "pending", 0]);
    expect(approvalsKeys.count()).toEqual(["approvals", "count"]);
    expect(approvalsKeys.detail(7)).toEqual(["approvals", "detail", 7]);
    expect(approvalsKeys.templates("ticket")).toEqual(["approvals", "templates", "ticket"]);
    expect(approvalsKeys.lists()).toEqual(["approvals", "lists"]);
    expect(approvalsKeys.delegations()).toEqual(["approvals", "delegations"]);
    expect(approvalsKeys.ticket(130)).toEqual(["tickets", 130, "approvals"]);
  });

  it("builds the public link routes with the token only in the body", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await approvalLinkApi.inspect("tok123");
    await approvalLinkApi.decide("tok123", "approved");
    await approvalLinkApi.decide("tok123", "rejected", "Zu riskant");
    expect(calls(spy)).toEqual([
      ["POST", "/api/v1/approval-links/inspect", undefined, { token: "tok123" }],
      ["POST", "/api/v1/approval-links/decide", undefined, { token: "tok123", decision: "approved", comment: null }],
      ["POST", "/api/v1/approval-links/decide", undefined, { token: "tok123", decision: "rejected", comment: "Zu riskant" }],
    ]);
  });

  it("builds the portal routes on the portal client", async () => {
    const spy = vi.spyOn(portalApi, "request").mockResolvedValue({} as never);
    await portalApprovalsApi.list({ state: "pending", limit: 1 });
    await portalApprovalsApi.get(601);
    await portalApprovalsApi.decide(601, { decision: "approved", comment: null });
    expect(calls(spy)).toEqual([
      ["GET", "/api/portal/approvals", { state: "pending", limit: 1 }, undefined],
      ["GET", "/api/portal/approvals/601", undefined, undefined],
      ["POST", "/api/portal/approvals/601/decision", undefined, { decision: "approved", comment: null }],
    ]);
  });

  it("builds the admin routes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await approvalsAdminApi.overview({ status: "pending", overdue: true, limit: 25 });
    await approvalsAdminApi.templates();
    await approvalsAdminApi.validateTemplate({ steps: [] }, "ticket");
    await approvalsAdminApi.deleteTemplate(2);
    await approvalsAdminApi.createList({ name: "CAB Netzwerk", member_ids: [1, 2], valid: true });
    await approvalsAdminApi.updateList(1, { name: "CAB Netzwerk", member_ids: [1], valid: true });
    await approvalsAdminApi.delegations({ user_id: 1, include_ended: true });
    await approvalsAdminApi.createDelegation({ user_id: 1, substitute_user_id: 2 });
    expect(calls(spy)).toEqual([
      ["GET", "/api/v1/admin/approvals", { status: "pending", overdue: true, limit: 25 }, undefined],
      ["GET", "/api/v1/admin/approvals/templates", undefined, undefined],
      ["POST", "/api/v1/admin/approvals/templates/validate", undefined, { definition: { steps: [] }, subject_type: "ticket" }],
      ["DELETE", "/api/v1/admin/approvals/templates/2", undefined, undefined],
      ["POST", "/api/v1/admin/approvals/lists", undefined, { name: "CAB Netzwerk", member_ids: [1, 2], valid: true }],
      ["PUT", "/api/v1/admin/approvals/lists/1", undefined, { name: "CAB Netzwerk", member_ids: [1], valid: true }],
      ["GET", "/api/v1/admin/approvals/delegations", { user_id: 1, include_ended: true }, undefined],
      ["POST", "/api/v1/admin/approvals/delegations", undefined, { user_id: 1, substitute_user_id: 2 }],
    ]);
  });
});

describe("approvalErrorMessage", () => {
  const t = i18n.t.bind(i18n);

  it("maps a known code to its German text", async () => {
    await i18n.changeLanguage("de");
    expect(approvalErrorMessage(apiError(403, "not_an_approver"), t)).toBe(
      "Sie sind für diese Freigabe nicht als Freigeber vorgesehen.",
    );
    expect(approvalErrorMessage(apiError(409, "already_decided"), t)).toBe("Hier wurde bereits entschieden.");
    expect(approvalErrorMessage(apiError(403, "delegation_not_active"), t)).toContain("Vertretung ist nicht (mehr) aktiv");
  });

  it("maps a known code to its English text", async () => {
    await i18n.changeLanguage("en");
    expect(approvalErrorMessage(apiError(410, "link_used"), t)).toBe("A decision was already made with this link.");
  });

  it("falls back for an unknown code, a bare 429 and a non-API error", async () => {
    await i18n.changeLanguage("de");
    const generic = "Die Freigabe konnte nicht verarbeitet werden. Bitte versuchen Sie es erneut.";
    expect(approvalErrorMessage(apiError(500, "something_new"), t)).toBe(generic);
    expect(approvalErrorMessage(new Error("boom"), t)).toBe(generic);
    expect(approvalErrorMessage(null, t)).toBe(generic);
    expect(approvalErrorMessage(new ApiError(429, "Too Many Requests", "/x"), t)).toBe(
      "Zu viele Versuche. Bitte warten Sie einige Minuten.",
    );
  });

  it("reads the code from the {detail: {code}} body only", () => {
    expect(approvalErrorCode(apiError(404, "approval_not_found"))).toBe("approval_not_found");
    expect(approvalErrorCode(new ApiError(404, { detail: "Not Found" }, "/x"))).toBeNull();
    expect(approvalErrorCode(new Error("x"))).toBeNull();
  });
});

describe("approval error codes", () => {
  const lookup = (tree: unknown, dotted: string) =>
    dotted.split(".").reduce<unknown>((cur, part) => (cur && typeof cur === "object" ? (cur as Record<string, unknown>)[part] : undefined), tree);

  it.each([
    ["en", en],
    ["de", de],
  ])("has an approvals.errors text in %s for every code", (_lang, tree) => {
    for (const code of [...APPROVAL_ERROR_CODES, "unknown"]) {
      expect(typeof lookup(tree, `approvals.errors.${code}`), code).toBe("string");
    }
  });

  const contract = resolve(process.cwd(), "../backend/src/tiqora/approvals/contract.py");
  it.skipIf(!existsSync(contract))("covers every ERROR_STATUS code of the backend plus rate_limited", () => {
    const src = readFileSync(contract, "utf8");
    const block = src.slice(src.indexOf("ERROR_STATUS: Final"));
    const body = block.slice(block.indexOf("{"), block.indexOf("}"));
    const backend = [...body.matchAll(/"([a-z_]+)":\s*\d+/g)].map((m) => m[1]);
    expect(backend.length).toBeGreaterThanOrEqual(28);
    expect([...APPROVAL_ERROR_CODES].sort()).toEqual([...backend, "rate_limited"].sort());
  });
});

describe("approvalRuleKey", () => {
  it("maps the plain rule of the link API to the portal's rule_text_key", () => {
    expect(approvalRuleKey("majority")).toBe("approvals.rule.majority");
    expect(i18n.t(approvalRuleKey("quorum"), { required: 3, lng: "de" })).toBe("3 Freigaben nötig");
    expect(i18n.t(approvalRuleKey("majority"), { required: 2, lng: "en" })).toBe("Majority needed (2 approvals)");
    expect(i18n.t(approvalRuleKey("any"), { lng: "de" })).toBe("Eine Freigabe genügt");
    expect(i18n.t(approvalRuleKey("all"), { lng: "en" })).toBe("All approvers must approve");
  });
});
