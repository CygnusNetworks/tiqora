import { describe, expect, it } from "vitest";
import { moduleSwitchReply, type ModuleSwitches } from "./moduleSwitches";

const ON: ModuleSwitches = { approvalsOn: true, changeOff: null };

describe("moduleSwitchReply", () => {
  it("leaves everything alone while both are on, but the status route", () => {
    expect(moduleSwitchReply("/api/v1/approvals/status", ON)).toEqual({ status: 200, body: { enabled: true } });
    for (const path of ["/api/v1/approvals/inbox", "/api/v1/itsm/changes", "/api/v1/approval-links/inspect", "/api/v1/tickets"]) {
      expect(moduleSwitchReply(path, ON)).toBeUndefined();
    }
  });

  it("refuses like the backend while approvals are off: 404 for sessions, 409 for the public link", () => {
    const off: ModuleSwitches = { approvalsOn: false, changeOff: "approvals_off" };
    const code = { detail: { code: "approvals_disabled", detail: null } };
    expect(moduleSwitchReply("/api/v1/approvals/status", off)?.body).toEqual({ enabled: false });
    expect(moduleSwitchReply("/api/v1/approvals/inbox", off)).toEqual({ status: 404, body: code });
    for (const read of ["lists", "resolvers", "7"]) expect(moduleSwitchReply(`/api/v1/approvals/${read}`, off)).toBeUndefined();
    expect(moduleSwitchReply("/api/v1/tickets/1/approvals", off)).toEqual({ status: 404, body: code });
    expect(moduleSwitchReply("/api/portal/approvals", off)).toEqual({ status: 404, body: code });
    expect(moduleSwitchReply("/api/v1/approval-links/inspect", off)).toEqual({ status: 409, body: code });
    expect(moduleSwitchReply("/api/v1/itsm/changes/status", off)?.body).toMatchObject({ reason: "approvals_off", available: false, legacy: "none" });
  });

  it("refuses the change routes, the legacy views included, and not the CMDB", () => {
    const off: ModuleSwitches = { approvalsOn: true, changeOff: "change_off" };
    for (const path of ["/api/v1/itsm/changes", "/api/v1/itsm/changes/5", "/api/v1/itsm/legacy-changes", "/api/v1/itsm/cabs", "/api/v1/tickets/5/change"]) {
      expect(moduleSwitchReply(path, off)?.status, path).toBe(404);
    }
    expect(moduleSwitchReply("/api/v1/itsm/cis", off)).toBeUndefined();
    expect(moduleSwitchReply("/api/v1/approvals/inbox", off)).toBeUndefined();
  });
});
