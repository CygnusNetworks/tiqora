import { afterEach, describe, expect, it } from "vitest";
import { DEMO_APPROVAL_CHANGE_TICKET_ID } from "./approvalsMock";
import { DEMO_CHANGE_JOB_ID, resolveChangeData } from "./changeMock";
import { DEMO_ITSM_MODE_KEY } from "./itsmMode";
import { resolveData } from "./mockData";
import { PHASE_PATH } from "@/lib/changePhases";
import type {
  Change,
  ChangeApproval,
  ChangeCalendar,
  ChangeImpact,
  ChangePage,
  ChangeRisk,
  ChangeStatus,
  ChangeTask,
  CabMeeting,
  FreezeOccurrences,
  LegacyChange,
  LegacyChangePage,
  ServiceAvailability,
} from "@/lib/changeApi";
import type { PortalChange } from "@/lib/portalChangeApi";

afterEach(() => localStorage.removeItem(DEMO_ITSM_MODE_KEY));

const get = (path: string, search = "") => resolveChangeData(path, "GET", search, "tiqora");

describe("change demo mock (Mode B)", () => {
  it("answers the status per mode", () => {
    expect(resolveChangeData("/api/v1/itsm/changes/status", "GET", "", "tiqora")).toMatchObject({ mode: "tiqora", available: true, reason: "ok" });
    expect(resolveChangeData("/api/v1/itsm/changes/status", "GET", "", "znuny_readthrough")).toMatchObject({
      available: false,
      reason: "mode_a",
      legacy: "live",
      can_see_legacy: true,
    });
    expect(resolveChangeData("/api/v1/itsm/changes/status", "GET", "", "off")).toMatchObject({ available: false, reason: "itsm_off", legacy: "none" });
  });

  it("lists six changes across the phases with a type for each path", () => {
    const page = get("/api/v1/itsm/changes") as ChangePage;
    expect(page.total).toBe(6);
    expect(page.truncated).toBe(false);
    expect(new Set(page.items.map((c) => c.phase)).size).toBeGreaterThanOrEqual(5);
    expect(new Set(page.items.map((c) => c.change_type))).toEqual(new Set(["standard", "normal", "emergency"]));
  });

  it("filters the list by phase, type and text", () => {
    const approval = get("/api/v1/itsm/changes", "?phase=approval&phase=scheduled") as ChangePage;
    expect(approval.items.map((c) => c.phase).sort()).toEqual(["approval", "scheduled"]);
    const emergency = get("/api/v1/itsm/changes", "?change_type=emergency") as ChangePage;
    expect(emergency.items).toHaveLength(1);
    const text = get("/api/v1/itsm/changes", "?q=firmware") as ChangePage;
    expect(text.items.map((c) => c.ticket_id)).toEqual([DEMO_APPROVAL_CHANGE_TICKET_ID]);
  });

  it("shows the CAB change of the approvals demo with the same approval", () => {
    const change = get(`/api/v1/itsm/changes/${DEMO_APPROVAL_CHANGE_TICKET_ID}`) as Change;
    expect(change).toMatchObject({ phase: "approval", change_type: "normal", approval_round: 1, approval_via: "cab" });
    const approval = get(`/api/v1/itsm/changes/${DEMO_APPROVAL_CHANGE_TICKET_ID}/approval`) as ChangeApproval;
    expect(approval.snapshot).toMatchObject({ approval_id: 501, approval_key: "change_cab", status: "pending", round: 1 });
    const detail = resolveData("/api/v1/approvals/501", "GET") as { subject_type: string; subject_ref: string; ticket_id: number; approval_key: string };
    expect(detail).toMatchObject({ subject_type: "itsm.change", subject_ref: "1", ticket_id: DEMO_APPROVAL_CHANGE_TICKET_ID, approval_key: "change_cab" });
  });

  it("only offers allowed targets that the phase path of the type permits", () => {
    const page = get("/api/v1/itsm/changes") as ChangePage;
    for (const item of page.items) {
      const change = get(`/api/v1/itsm/changes/${item.ticket_id}`) as Change;
      expect(change.phase).toBe(item.phase);
      expect(PHASE_PATH[change.change_type]).toContain(change.phase === "rejected" || change.phase === "cancelled" ? "draft" : change.phase);
      if (change.change_type === "standard") {
        expect(change.allowed_targets).not.toContain("submitted");
        expect(change.allowed_targets).not.toContain("approval");
      }
    }
  });

  it("has an emergency change that collides with a freeze window", () => {
    const conflicts = get("/api/v1/itsm/changes/132/conflicts") as { kind: string; severity: string; ref: string }[];
    expect(conflicts).toEqual([expect.objectContaining({ kind: "freeze", ref: "freeze:1" })]);
    const calendar = get("/api/v1/itsm/change-calendar", "?start=2026-10-01T00:00:00Z&end=2026-11-01T00:00:00Z") as ChangeCalendar;
    expect(calendar.changes.find((c) => c.ticket_id === 132)).toMatchObject({ conflict_kinds: ["freeze"], conflict_severity: "warn" });
    expect(calendar.freezes.map((f) => f.freeze_id)).toEqual([1, 2]);
  });

  it("answers every GET of the agent API surface (I18)", () => {
    const id = DEMO_APPROVAL_CHANGE_TICKET_ID;
    const paths = [
      "/api/v1/itsm/changes/status",
      "/api/v1/itsm/changes",
      `/api/v1/itsm/changes/${id}`,
      `/api/v1/itsm/changes/${id}/events`,
      `/api/v1/itsm/changes/${id}/impact`,
      `/api/v1/itsm/changes/${id}/conflicts`,
      `/api/v1/itsm/changes/${id}/risk`,
      `/api/v1/itsm/changes/${id}/tasks`,
      `/api/v1/itsm/changes/${id}/related`,
      `/api/v1/itsm/changes/${id}/approval`,
      "/api/v1/itsm/change-tasks/mine",
      "/api/v1/itsm/change-calendar",
      "/api/v1/itsm/change-calendar/services",
      "/api/v1/itsm/change-templates",
      "/api/v1/itsm/freeze-windows",
      "/api/v1/itsm/cabs",
      "/api/v1/itsm/cab-meetings",
      "/api/v1/itsm/cab-meetings/1",
      "/api/v1/itsm/cab-meetings/1/suggestions",
      "/api/v1/itsm/legacy-changes",
      "/api/v1/itsm/legacy-changes/11",
    ];
    for (const path of paths) expect(get(path), path).toBeDefined();
  });

  it("answers every GET of the admin API surface", () => {
    const base = "/api/v1/admin/itsm/change";
    for (const path of ["templates", "templates/1", "cabs", "risk-questions", "freeze-windows", "legacy-archive"]) {
      expect(get(`${base}/${path}`), path).toBeDefined();
    }
    expect(get(`${base}/legacy-archive`)).toMatchObject({ can_start: true, archived: 3 });
  });

  it("answers the portal schedule with scheduled, running and closed windows of portal-visible changes", () => {
    const schedule = get("/api/portal/change-schedule") as PortalChange[];
    expect(schedule.length).toBeGreaterThan(0);
    for (const entry of schedule) {
      expect(["scheduled", "implementing", "closed"]).toContain(entry.phase);
      expect(entry.services.length).toBeGreaterThan(0);
    }
  });

  it("returns undefined for an unknown change and for foreign paths", () => {
    expect(get("/api/v1/itsm/changes/999")).toBeUndefined();
    expect(get("/api/v1/itsm/cis")).toBeUndefined();
    expect(get("/api/v1/tickets/130/itsm")).toBeUndefined();
  });

  it("has a risk questionnaire and a finding per risky change", () => {
    const risk = get("/api/v1/itsm/changes/132/risk") as ChangeRisk;
    expect(risk.questions.length).toBeGreaterThan(0);
    expect(risk.findings.map((f) => f.check_key)).toContain("core.freeze");
    expect(risk.thresholds.very_high_from).toBeGreaterThan(risk.thresholds.high_from);
    const draft = get("/api/v1/itsm/changes/134/risk") as ChangeRisk;
    expect(draft).toMatchObject({ incomplete: true, answers: {}, level: null });
  });

  it("lists impact, tasks and the my-tasks view consistently", () => {
    const impact = get(`/api/v1/itsm/changes/${DEMO_APPROVAL_CHANGE_TICKET_ID}/impact`) as ChangeImpact;
    expect(impact.services.map((s) => s.service_id)).toEqual([1, 3]);
    expect(impact.cis.length).toBeGreaterThan(0);
    const tasks = get(`/api/v1/itsm/changes/${DEMO_APPROVAL_CHANGE_TICKET_ID}/tasks`) as ChangeTask[];
    expect(tasks.map((t) => t.position)).toEqual([1, 2, 3]);
    const open = get("/api/v1/itsm/change-tasks/mine", "?open_only=true") as { ticket_id: number; finished: boolean }[];
    expect(open.every((t) => !t.finished)).toBe(true);
    const all = get("/api/v1/itsm/change-tasks/mine", "?open_only=false") as { ticket_id: number }[];
    expect(all.length).toBeGreaterThan(open.length);
  });

  it("limits the calendar and the service view to the requested window", () => {
    const range = "?start=2026-11-01T00:00:00Z&end=2026-12-01T00:00:00Z";
    const calendar = get("/api/v1/itsm/change-calendar", range) as ChangeCalendar;
    expect(calendar.changes.map((c) => c.ticket_id)).toEqual([133]);
    const services = get("/api/v1/itsm/change-calendar/services", range) as ServiceAvailability[];
    expect(services.find((s) => s.service_id === 2)).toMatchObject({ change_count: 1, minutes_affected: 240 });
    const freezes = get("/api/v1/itsm/freeze-windows", range) as FreezeOccurrences;
    expect(freezes.items).toEqual([]);
  });

  it("has a CAB meeting with an agenda that includes the approval snapshot", () => {
    const meeting = get("/api/v1/itsm/cab-meetings/1") as CabMeeting;
    expect(meeting.agenda).toHaveLength(meeting.agenda_count);
    expect(meeting.agenda[0].approval).toMatchObject({ approval_id: 501 });
  });

  it("answers the writes with a plausible record and the job as done", () => {
    expect(resolveChangeData("/api/v1/itsm/changes", "POST", "", "tiqora")).toMatchObject({ tasks_job_id: DEMO_CHANGE_JOB_ID });
    expect(resolveChangeData("/api/v1/itsm/changes/130/transition", "POST", "", "tiqora")).toMatchObject({ ticket_id: 130 });
    expect(resolveChangeData("/api/v1/itsm/changes/130/impact/recompute", "POST", "", "tiqora")).toEqual({ job_id: DEMO_CHANGE_JOB_ID, status: "queued" });
    expect(resolveChangeData(`/api/v1/jobs/${DEMO_CHANGE_JOB_ID}`, "GET", "", "tiqora")).toMatchObject({ status: "succeeded" });
    expect(resolveChangeData("/api/v1/admin/itsm/change/templates/1", "DELETE", "", "tiqora")).toBeNull();
  });
});

describe("change demo mock (Mode A and off)", () => {
  it("serves the live Znuny changes and nothing else in Mode A", () => {
    const page = resolveChangeData("/api/v1/itsm/legacy-changes", "GET", "", "znuny_readthrough") as LegacyChangePage;
    expect(page.source).toBe("live");
    expect(page.items.length).toBe(2);
    const detail = resolveChangeData("/api/v1/itsm/legacy-changes/11", "GET", "", "znuny_readthrough") as LegacyChange;
    expect(detail.source).toBe("live");
    expect(detail.znuny_url).toContain("ChangeID=11");
    expect(detail.workorders).toHaveLength(2);
    expect(resolveChangeData("/api/v1/itsm/changes", "GET", "", "znuny_readthrough")).toBeUndefined();
    expect(resolveChangeData("/api/portal/change-schedule", "GET", "", "znuny_readthrough")).toBeUndefined();
  });

  it("filters the legacy list by ticket and CI link", () => {
    const byTicket = resolveChangeData("/api/v1/itsm/legacy-changes", "GET", "?ticket_id=100", "znuny_readthrough") as LegacyChangePage;
    expect(byTicket.items.map((c) => c.znuny_change_id)).toEqual([11]);
    const byCi = resolveChangeData("/api/v1/itsm/legacy-changes", "GET", "?ci=znuny:999", "znuny_readthrough") as LegacyChangePage;
    expect(byCi.items).toEqual([]);
  });

  it("marks the same data as archive in Mode B and drops the Znuny link", () => {
    const detail = get("/api/v1/itsm/legacy-changes/11") as LegacyChange;
    expect(detail).toMatchObject({ source: "archive", znuny_url: null });
  });

  it("answers nothing in mode off except the status", () => {
    expect(resolveChangeData("/api/v1/itsm/legacy-changes", "GET", "", "off")).toBeUndefined();
    expect(resolveChangeData("/api/v1/itsm/changes", "GET", "", "off")).toBeUndefined();
    expect((resolveChangeData("/api/v1/itsm/changes/status", "GET", "", "off") as ChangeStatus).mode).toBe("off");
  });

  it("is reached through resolveData and follows the demo mode", () => {
    expect((resolveData("/api/v1/itsm/changes/status", "GET") as ChangeStatus).available).toBe(true);
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "znuny_readthrough");
    expect(resolveData("/api/v1/itsm/changes/status", "GET")).toMatchObject({ legacy: "live" });
  });
});
