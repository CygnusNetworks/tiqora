import { afterEach, describe, expect, it } from "vitest";
import { DEMO_ITSM_MODE_KEY } from "./itsmMode";
import { resolveItsmData } from "./itsmMock";
import { resolveData } from "./mockData";
import type { CiDetail, CiPage, CiSummary, ItsmStatus, Problem, TicketItsm } from "@/lib/itsmApi";

afterEach(() => localStorage.removeItem(DEMO_ITSM_MODE_KEY));

describe("itsm demo mock (Mode A data)", () => {
  it("answers /itsm/status from the demo mode", () => {
    expect((resolveItsmData("/api/v1/itsm/status", "GET") as ItsmStatus).mode).toBe("tiqora");
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "znuny_readthrough");
    const a = resolveItsmData("/api/v1/itsm/status", "GET") as ItsmStatus;
    expect(a).toMatchObject({ mode: "znuny_readthrough", ci_available: true, ci_source: "znuny", ci_read_only: true });
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "off");
    expect(resolveItsmData("/api/v1/itsm/status", "GET")).toMatchObject({ mode: "off", ci_available: false, ci_source: null });
  });

  it("returns three CIs and filters them", () => {
    const all = resolveItsmData("/api/v1/itsm/cis", "GET") as CiPage;
    expect(all.items).toHaveLength(3);
    expect(all.total).toBe(3);
    const q = resolveItsmData("/api/v1/itsm/cis", "GET", "?q=SRV-web") as CiPage;
    expect(q.items.map((c) => c.ref)).toEqual(["znuny:201"]);
    const inci = resolveItsmData("/api/v1/itsm/cis", "GET", "?inci=42") as CiPage;
    expect(inci.items.map((c) => c.ref)).toEqual(["znuny:202"]);
  });

  it("returns a CI with a nested attribute tree and a CI reference", () => {
    const d = resolveItsmData("/api/v1/itsm/cis/znuny/201", "GET") as CiDetail;
    expect(d.read_only).toBe(true);
    expect(d.attributes.find((a) => a.key === "NIC")?.children).toHaveLength(2);
    expect(d.attributes.find((a) => a.kind === "ci")?.ref).toBe("znuny:202");
    expect(resolveItsmData("/api/v1/itsm/cis/znuny/9999", "GET")).toBeUndefined();
  });

  it("returns a problem with two incidents and an active major incident", () => {
    const p = resolveItsmData("/api/v1/tickets/101/problem", "GET") as Problem;
    expect(p.incidents).toHaveLength(2);
    const m = resolveItsmData("/api/v1/tickets/100/itsm", "GET") as TicketItsm;
    expect(m.major_incident?.active).toBe(true);
    expect(m.role).toBe("major_incident");
  });

  it("is wired into resolveData and ignores other paths", () => {
    expect(resolveData("/api/v1/itsm/catalog", "GET")).toBeDefined();
    expect(resolveItsmData("/api/v1/tickets/dashboard-summary", "GET")).toBeUndefined();
  });

  it("uses example.org data only", () => {
    const text = JSON.stringify(resolveItsmData("/api/v1/itsm/cis/znuny/201", "GET"));
    expect(text).not.toMatch(/@(?!example\.org)/);
  });

  it("returns the CIs of a service", () => {
    const list = resolveItsmData("/api/v1/itsm/services/1/cis", "GET") as CiSummary[];
    expect(list.map((c) => c.ref)).toEqual(["znuny:201", "znuny:202"]);
  });
});
