import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";
import { itsmApi, itsmErrorCode, itsmErrorKey, itsmKeys } from "./itsmApi";

afterEach(() => vi.restoreAllMocks());

function calls(spy: { mock: { calls: unknown[][] } }) {
  return spy.mock.calls.map((c) => [c[0], c[1], (c[2] as { query?: unknown; body?: unknown } | undefined)?.query]);
}

describe("itsmApi paths and queries", () => {
  it("builds the agent CI routes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await itsmApi.status();
    await itsmApi.catalog();
    await itsmApi.cis({ q: "srv", class: ["11", "12"], depl: ["31"], inci: [], limit: 25, offset: 50 });
    await itsmApi.ci("znuny", 201);
    await itsmApi.ci("znuny", 201, 902);
    await itsmApi.ciVersions("tiqora", 7, { limit: 10, offset: 0 });
    await itsmApi.ciTickets("znuny", 201);
    expect(calls(spy)).toEqual([
      ["GET", "/api/v1/itsm/status", undefined],
      ["GET", "/api/v1/itsm/catalog", undefined],
      ["GET", "/api/v1/itsm/cis", { q: "srv", class: ["11", "12"], depl: ["31"], inci: [], limit: 25, offset: 50 }],
      ["GET", "/api/v1/itsm/cis/znuny/201", { version_id: undefined }],
      ["GET", "/api/v1/itsm/cis/znuny/201", { version_id: 902 }],
      ["GET", "/api/v1/itsm/cis/tiqora/7/versions", { limit: 10, offset: 0 }],
      ["GET", "/api/v1/itsm/cis/znuny/201/tickets", undefined],
    ]);
  });

  it("builds the lookup routes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await itsmApi.linkTypes();
    await itsmApi.linkTypes("ticket_ci");
    await itsmApi.prioritySuggestion("5 sehr hoch", "3 normal");
    await itsmApi.serviceIncidentStates([3, 4]);
    await itsmApi.recentIncident(9, 100);
    await itsmApi.service(9);
    expect(calls(spy)).toEqual([
      ["GET", "/api/v1/itsm/link-types", { pair: "ticket_ticket" }],
      ["GET", "/api/v1/itsm/link-types", { pair: "ticket_ci" }],
      ["GET", "/api/v1/itsm/priority-suggestion", { criticality: "5 sehr hoch", impact: "3 normal" }],
      ["GET", "/api/v1/itsm/services/incident-states", { ids: [3, 4] }],
      ["GET", "/api/v1/itsm/services/9/recent-incident", { exclude_ticket_id: 100 }],
      ["GET", "/api/v1/itsm/services/9", undefined],
    ]);
  });

  it("builds the ticket-scoped routes with bodies", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await itsmApi.ticketItsm(100);
    await itsmApi.ticketCis(100);
    await itsmApi.linkCi(100, { ci: "znuny:201" });
    await itsmApi.unlinkCi(100, "znuny", 201, "RelevantTo");
    await itsmApi.unlinkTicket(100, 101, "ParentChild");
    await itsmApi.linkGraph(100, 2);
    await itsmApi.linkedArticles(100, { target_ticket_ids: [101], mode: "note", subject: "s", body_html: "<p>x</p>", visible_for_customer: false });
    await itsmApi.problem.addIncidents(101, [100, 102]);
    await itsmApi.problem.closeIncidents(101);
    await itsmApi.majorIncident.postUpdate(100, { mode: "internal", body_html: "<p>x</p>" });
    expect(calls(spy)).toEqual([
      ["GET", "/api/v1/tickets/100/itsm", undefined],
      ["GET", "/api/v1/tickets/100/cis", undefined],
      ["POST", "/api/v1/tickets/100/cis", undefined],
      ["DELETE", "/api/v1/tickets/100/cis/znuny/201", { link_type: "RelevantTo" }],
      ["DELETE", "/api/v1/tickets/100/links/101", { link_type: "ParentChild" }],
      ["GET", "/api/v1/tickets/100/link-graph", { depth: 2 }],
      ["POST", "/api/v1/tickets/100/linked-articles", undefined],
      ["POST", "/api/v1/tickets/101/problem/incidents", undefined],
      ["POST", "/api/v1/tickets/101/problem/close-incidents", undefined],
      ["POST", "/api/v1/tickets/100/major-incident/updates", undefined],
    ]);
    expect(spy.mock.calls[2][2]).toMatchObject({ body: { ci: "znuny:201" } });
    expect(spy.mock.calls[7][2]).toMatchObject({ body: { incident_ticket_ids: [100, 102] } });
    expect(spy.mock.calls[8][2]).toMatchObject({ body: { note_html: null } });
  });

  it("builds the admin routes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await itsmApi.admin.get();
    await itsmApi.admin.setMode("off");
    await itsmApi.admin.patchLinkType(4, { propagation: "both" });
    await itsmApi.admin.syncLinkTypes();
    await itsmApi.admin.serviceMeta(9);
    expect(calls(spy)).toEqual([
      ["GET", "/api/v1/admin/itsm", undefined],
      ["PUT", "/api/v1/admin/itsm/mode", undefined],
      ["PATCH", "/api/v1/admin/itsm/link-types/4", undefined],
      ["POST", "/api/v1/admin/itsm/link-types/sync", undefined],
      ["GET", "/api/v1/admin/itsm/services/9/meta", undefined],
    ]);
    expect(spy.mock.calls[1][2]).toMatchObject({ body: { mode: "off" } });
  });
});

describe("itsmKeys", () => {
  it("uses the documented query keys", () => {
    expect(itsmKeys.status()).toEqual(["itsm", "status"]);
    expect(itsmKeys.cis({ q: "a" })).toEqual(["itsm", "cis", { q: "a" }]);
    expect(itsmKeys.ci("znuny", 201, 902)).toEqual(["itsm", "ci", "znuny", 201, 902]);
    expect(itsmKeys.ci("znuny", 201)).toEqual(["itsm", "ci", "znuny", 201, null]);
    expect(itsmKeys.ticketItsm(5)).toEqual(["tickets", 5, "itsm"]);
    expect(itsmKeys.ticketCis(5)).toEqual(["tickets", 5, "cis"]);
    expect(itsmKeys.linkGraph(5)).toEqual(["tickets", 5, "link-graph", null]);
    expect(itsmKeys.service(9)).toEqual(["itsm", "services", 9]);
  });
});

describe("error codes", () => {
  it("reads the code of the API error body and maps it to a message key", () => {
    const err = new ApiError(409, { detail: { code: "itsm_off" } }, "/api/v1/itsm/cis");
    expect(itsmErrorCode(err)).toBe("itsm_off");
    expect(itsmErrorKey(err)).toBe("itsm.errors.itsm_off");
  });

  it("falls back to the generic message for unknown codes and other errors", () => {
    expect(itsmErrorKey(new ApiError(409, { detail: { code: "whatever" } }, "/x"))).toBe("itsm.errors.generic");
    expect(itsmErrorKey(new Error("boom"))).toBe("itsm.errors.generic");
    expect(itsmErrorCode(new ApiError(500, "oops", "/x"))).toBeNull();
  });
});
