import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";
import { changeApi, changeErrorCode, changeKeys, changeNav } from "./changeApi";
import { changeAdminApi } from "./changeAdminApi";
import { portalApi } from "./portalApi";
import { portalChangeApi } from "./portalChangeApi";

afterEach(() => vi.restoreAllMocks());

function calls(spy: { mock: { calls: unknown[][] } }) {
  return spy.mock.calls.map((c) => [c[0], c[1], (c[2] as { query?: unknown; body?: unknown } | undefined)?.query]);
}

function bodies(spy: { mock: { calls: unknown[][] } }) {
  return spy.mock.calls.map((c) => [c[0], c[1], (c[2] as { body?: unknown } | undefined)?.body]);
}

describe("changeApi paths and queries", () => {
  it("builds the change routes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await changeApi.status();
    await changeApi.list({ q: "switch", phase: ["approval", "scheduled"], change_type: ["normal"], service_id: 3, mine: true, order: "planned_start", limit: 25, offset: 50 });
    await changeApi.get(130);
    await changeApi.events(130, { limit: 20, before_id: 9 });
    await changeApi.impact(130);
    await changeApi.conflicts(130);
    await changeApi.risk(130);
    await changeApi.tasks(130);
    await changeApi.related(130);
    await changeApi.approval(130);
    expect(calls(spy)).toEqual([
      ["GET", "/api/v1/itsm/changes/status", undefined],
      [
        "GET",
        "/api/v1/itsm/changes",
        {
          q: "switch",
          phase: ["approval", "scheduled"],
          change_type: ["normal"],
          service_id: 3,
          ci_id: undefined,
          window_start: undefined,
          window_end: undefined,
          mine: true,
          order: "planned_start",
          limit: 25,
          offset: 50,
        },
      ],
      ["GET", "/api/v1/itsm/changes/130", undefined],
      ["GET", "/api/v1/itsm/changes/130/events", { limit: 20, before_id: 9 }],
      ["GET", "/api/v1/itsm/changes/130/impact", undefined],
      ["GET", "/api/v1/itsm/changes/130/conflicts", undefined],
      ["GET", "/api/v1/itsm/changes/130/risk", undefined],
      ["GET", "/api/v1/itsm/changes/130/tasks", undefined],
      ["GET", "/api/v1/itsm/changes/130/related", undefined],
      ["GET", "/api/v1/itsm/changes/130/approval", undefined],
    ]);
  });

  it("builds the calendar, task, template and CAB routes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await changeApi.myTasks();
    await changeApi.myTasks(false);
    await changeApi.calendar({ start: "2026-10-01T00:00:00Z", end: "2026-11-01T00:00:00Z", service_id: [1, 2], include_tasks: true });
    await changeApi.calendarServices("2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z");
    await changeApi.templates();
    await changeApi.freezeWindows("2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z");
    await changeApi.cabs();
    await changeApi.meetings("2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z");
    await changeApi.meeting(1);
    await changeApi.meetingSuggestions(1);
    expect(calls(spy)).toEqual([
      ["GET", "/api/v1/itsm/change-tasks/mine", { open_only: true }],
      ["GET", "/api/v1/itsm/change-tasks/mine", { open_only: false }],
      ["GET", "/api/v1/itsm/change-calendar", { start: "2026-10-01T00:00:00Z", end: "2026-11-01T00:00:00Z", service_id: [1, 2], include_tasks: true }],
      ["GET", "/api/v1/itsm/change-calendar/services", { start: "2026-10-01T00:00:00Z", end: "2026-11-01T00:00:00Z" }],
      ["GET", "/api/v1/itsm/change-templates", undefined],
      ["GET", "/api/v1/itsm/freeze-windows", { start: "2026-10-01T00:00:00Z", end: "2026-11-01T00:00:00Z" }],
      ["GET", "/api/v1/itsm/cabs", undefined],
      ["GET", "/api/v1/itsm/cab-meetings", { start: "2026-10-01T00:00:00Z", end: "2026-11-01T00:00:00Z" }],
      ["GET", "/api/v1/itsm/cab-meetings/1", undefined],
      ["GET", "/api/v1/itsm/cab-meetings/1/suggestions", undefined],
    ]);
  });

  it("builds the legacy change routes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await changeApi.legacyList({ q: "switch", ticket_id: 100, ci: "znuny:202", limit: 50, offset: 0 });
    await changeApi.legacy(11);
    expect(calls(spy)).toEqual([
      ["GET", "/api/v1/itsm/legacy-changes", { q: "switch", ticket_id: 100, ci: "znuny:202", limit: 50, offset: 0 }],
      ["GET", "/api/v1/itsm/legacy-changes/11", undefined],
    ]);
  });

  it("sends the change writes with their bodies", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await changeApi.create({ title: "Neuer Change", change_type: "normal" });
    await changeApi.update(130, { reason: "Grund" });
    await changeApi.transition(130, { to: "scheduled", override_reason: "Notfall" });
    await changeApi.recomputeImpact(130);
    await changeApi.saveRiskAnswers(130, { tested: "yes" });
    await changeApi.evaluateRisk(130);
    await changeApi.overrideRisk(130, { level: "high", reason: "Kritischer Dienst" });
    await changeApi.createTask(130, { title: "Aufgabe" });
    await changeApi.updateTask(130, 141, { owner_id: 2 });
    await changeApi.orderTasks(130, [142, 141]);
    await changeApi.addRelated(130, 100);
    await changeApi.removeRelated(130, 100);
    await changeApi.adopt(7, "standard");
    await changeApi.createFrom(7, { title: "Aus Ticket", change_type: "normal" });
    expect(bodies(spy)).toEqual([
      ["POST", "/api/v1/itsm/changes", { title: "Neuer Change", change_type: "normal" }],
      ["PATCH", "/api/v1/itsm/changes/130", { reason: "Grund" }],
      ["POST", "/api/v1/itsm/changes/130/transition", { to: "scheduled", override_reason: "Notfall" }],
      ["POST", "/api/v1/itsm/changes/130/impact/recompute", undefined],
      ["PUT", "/api/v1/itsm/changes/130/risk/answers", { answers: { tested: "yes" } }],
      ["POST", "/api/v1/itsm/changes/130/risk/evaluate", undefined],
      ["PUT", "/api/v1/itsm/changes/130/risk/override", { level: "high", reason: "Kritischer Dienst" }],
      ["POST", "/api/v1/itsm/changes/130/tasks", { title: "Aufgabe" }],
      ["PATCH", "/api/v1/itsm/changes/130/tasks/141", { owner_id: 2 }],
      ["PUT", "/api/v1/itsm/changes/130/tasks/order", { order: [142, 141] }],
      ["POST", "/api/v1/itsm/changes/130/related", { ticket_id: 100 }],
      ["DELETE", "/api/v1/itsm/changes/130/related/100", undefined],
      ["POST", "/api/v1/tickets/7/change", { change_type: "standard" }],
      ["POST", "/api/v1/tickets/7/change/create-from", { title: "Aus Ticket", change_type: "normal" }],
    ]);
  });

  it("sends the meeting writes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await changeApi.createMeeting({ cab_id: 1, title: "CAB", starts_at: "2026-10-15T09:00:00Z", ends_at: "2026-10-15T10:00:00Z" });
    await changeApi.updateMeeting(1, { status: "held" });
    await changeApi.saveAgenda(1, { items: [] });
    expect(bodies(spy).map((b) => [b[0], b[1]])).toEqual([
      ["POST", "/api/v1/itsm/cab-meetings"],
      ["PATCH", "/api/v1/itsm/cab-meetings/1"],
      ["PUT", "/api/v1/itsm/cab-meetings/1/agenda"],
    ]);
  });
});

describe("changeAdminApi paths", () => {
  it("builds the admin routes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await changeAdminApi.templates();
    await changeAdminApi.template(2);
    await changeAdminApi.deleteTemplate(2);
    await changeAdminApi.cabs();
    await changeAdminApi.saveCabMembers(1, [{ member_kind: "user", member_ref: "1" }]);
    await changeAdminApi.riskQuestions();
    await changeAdminApi.orderRiskQuestions([2, 1]);
    await changeAdminApi.createRiskExamples();
    await changeAdminApi.freezeWindows();
    await changeAdminApi.deleteFreezeWindow(1);
    await changeAdminApi.legacyArchive();
    await changeAdminApi.startLegacyArchive({ dry_run: true });
    expect(bodies(spy)).toEqual([
      ["GET", "/api/v1/admin/itsm/change/templates", undefined],
      ["GET", "/api/v1/admin/itsm/change/templates/2", undefined],
      ["DELETE", "/api/v1/admin/itsm/change/templates/2", undefined],
      ["GET", "/api/v1/admin/itsm/change/cabs", undefined],
      ["PUT", "/api/v1/admin/itsm/change/cabs/1/members", [{ member_kind: "user", member_ref: "1" }]],
      ["GET", "/api/v1/admin/itsm/change/risk-questions", undefined],
      ["PUT", "/api/v1/admin/itsm/change/risk-questions/order", { order: [2, 1] }],
      ["POST", "/api/v1/admin/itsm/change/risk-questions/examples", undefined],
      ["GET", "/api/v1/admin/itsm/change/freeze-windows", undefined],
      ["DELETE", "/api/v1/admin/itsm/change/freeze-windows/1", undefined],
      ["GET", "/api/v1/admin/itsm/change/legacy-archive", undefined],
      ["POST", "/api/v1/admin/itsm/change/legacy-archive", { dry_run: true }],
    ]);
  });
});

describe("portalChangeApi", () => {
  it("asks the schedule for the window", async () => {
    const spy = vi.spyOn(portalApi, "request").mockResolvedValue([] as never);
    await portalChangeApi.list("2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z");
    expect(calls(spy)).toEqual([["GET", "/api/portal/change-schedule", { start: "2026-10-01T00:00:00Z", end: "2026-11-01T00:00:00Z" }]]);
  });
});

describe("changeKeys", () => {
  it("keeps every key under the change prefix", () => {
    const keys = [
      changeKeys.status(),
      changeKeys.list({}),
      changeKeys.detail(130),
      changeKeys.risk(130),
      changeKeys.calendar({ start: "a", end: "b" }),
      changeKeys.meeting(1),
      changeKeys.legacy(11),
    ];
    for (const key of keys) expect(key.slice(0, 2)).toEqual(["itsm", "changes"]);
  });
});

describe("changeErrorCode", () => {
  it("reads the code of the API's detail", () => {
    const err = new ApiError(409, { detail: { code: "change_mode_a" } }, "x");
    expect(changeErrorCode(err)).toBe("change_mode_a");
    expect(changeErrorCode(new Error("boom"))).toBeNull();
  });
});

describe("changeNav", () => {
  const base = { available: false, legacy: "none", can_see_legacy: false } as const;

  it("points to the change list when the change management is available", () => {
    expect(changeNav({ ...base, available: true })).toEqual({ to: "/agent/itsm/changes", legacy: false });
  });

  it("points to the Znuny changes for the live legacy source", () => {
    expect(changeNav({ ...base, legacy: "live", can_see_legacy: true })).toEqual({ to: "/agent/itsm/legacy-changes", legacy: true });
  });

  it("prefers the change list when both are there", () => {
    expect(changeNav({ available: true, legacy: "archive", can_see_legacy: true })).toEqual({ to: "/agent/itsm/changes", legacy: false });
  });

  it("hides the entry for mode off, an unreadable legacy source or no status", () => {
    expect(changeNav(base)).toBeNull();
    expect(changeNav({ ...base, legacy: "live", can_see_legacy: false })).toBeNull();
    expect(changeNav(undefined)).toBeNull();
  });
});
