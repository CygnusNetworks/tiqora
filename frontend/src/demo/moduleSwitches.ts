/**
 * The module switches of the demo build. E2E tests and anyone trying it out set
 * `localStorage["tiqora.demo.changeOff"] = "1"` or `["tiqora.demo.approvalsOff"] = "1"` before loading the app.
 */

export const DEMO_CHANGE_OFF_KEY = "tiqora.demo.changeOff";
export const DEMO_APPROVALS_OFF_KEY = "tiqora.demo.approvalsOff";

function isSet(key: string): boolean {
  try {
    return globalThis.localStorage?.getItem(key) === "1";
  } catch {
    return false;
  }
}

export const demoApprovalsOn = (): boolean => !isSet(DEMO_APPROVALS_OFF_KEY);

/** Why change management is off, like the backend: its own switch first, then the dependency on approvals. */
export function demoChangeSwitchedOff(): "change_off" | "approvals_off" | null {
  if (isSet(DEMO_CHANGE_OFF_KEY)) return "change_off";
  return demoApprovalsOn() ? null : "approvals_off";
}

export type ModuleSwitches = { approvalsOn: boolean; changeOff: "change_off" | "approvals_off" | null };

export const demoModuleSwitches = (): ModuleSwitches => ({ approvalsOn: demoApprovalsOn(), changeOff: demoChangeSwitchedOff() });

const APPROVALS_DISABLED = { detail: { code: "approvals_disabled", detail: null } };
/** Reads the admin pages share with agents: the backend leaves them open for admins, and the demo user is one. */
const ADMIN_READABLE = /\/api\/v1\/approvals\/(lists|resolvers|\d+)$/;
const CHANGE_UNAVAILABLE = { detail: { code: "change_unavailable" } };
const AGENT_APPROVALS = /\/api\/v1\/approvals(\/|$)|\/api\/v1\/tickets\/\d+\/approvals$/;
const CHANGE_ROUTES =
  /\/api\/v1\/itsm\/(changes|change-tasks|change-calendar|change-templates|freeze-windows|cabs|cab-meetings|legacy-changes)(\/|$)|\/api\/v1\/tickets\/\d+\/change(\/create-from)?$/;

/** What the backend answers because of a module switch; `undefined` for a route the switches leave alone. */
export function moduleSwitchReply(path: string, switches: ModuleSwitches): { status: number; body: unknown } | undefined {
  if (path.endsWith("/api/v1/approvals/status")) return { status: 200, body: { enabled: switches.approvalsOn } };
  if (!switches.approvalsOn && !ADMIN_READABLE.test(path)) {
    if (AGENT_APPROVALS.test(path) || path.includes("/api/portal/approvals")) return { status: 404, body: APPROVALS_DISABLED };
    if (path.includes("/api/v1/approval-links/")) return { status: 409, body: APPROVALS_DISABLED };
  }
  if (switches.changeOff === null) return undefined;
  if (path.endsWith("/api/v1/itsm/changes/status")) {
    return {
      status: 200,
      body: {
        mode: "tiqora",
        available: false,
        reason: switches.changeOff,
        legacy: "none",
        can_manage: true,
        can_see_legacy: false,
        setup_complete_change: true,
      },
    };
  }
  if (CHANGE_ROUTES.test(path)) return { status: 404, body: CHANGE_UNAVAILABLE };
  if (path.endsWith("/api/portal/change-schedule")) return { status: 404, body: { detail: "Not Found" } };
  return undefined;
}
