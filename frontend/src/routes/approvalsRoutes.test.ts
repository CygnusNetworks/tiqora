import { describe, expect, it } from "vitest";
import { router } from "@/router";
import { parseApprovalsSearch } from "./agent/approvalsSearch";

const paths = Object.keys(router.routesByPath);

describe("approval routes", () => {
  it.each([
    "/approve",
    "/agent/approvals",
    "/portal/approvals",
    "/portal/approvals/$approvalId",
    "/admin/approvals",
    "/admin/approvals/templates",
    "/admin/approvals/lists",
    "/admin/approvals/delegations",
  ])("registers %s", (path) => {
    expect(paths).toContain(path);
  });

  it("keeps /approve outside the agent, portal and admin layouts", () => {
    const route = (router.routesByPath as Record<string, { parentRoute?: { id: string } }>)["/approve"];
    expect(route.parentRoute?.id).toBe("__root__");
  });

  it("validates the agent search to {id?, tab?}", () => {
    expect(parseApprovalsSearch({ id: 501, tab: "done" })).toEqual({ id: 501, tab: "done" });
    expect(parseApprovalsSearch({ id: "42", tab: "delegation" })).toEqual({ id: 42, tab: "delegation" });
    expect(parseApprovalsSearch({ id: "abc", tab: "other" })).toEqual({ id: undefined, tab: undefined });
    expect(parseApprovalsSearch({ id: -3 })).toEqual({ id: undefined, tab: undefined });
    expect(parseApprovalsSearch({})).toEqual({ id: undefined, tab: undefined });
  });
});
