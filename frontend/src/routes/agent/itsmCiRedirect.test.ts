import { isRedirect } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";
import { router } from "@/router";

type BeforeLoad = (ctx: { params: { source: string; ciId: string } }) => unknown;

function beforeLoadOf(path: string): BeforeLoad {
  const route = (router.routesByPath as Record<string, { options: { beforeLoad?: BeforeLoad } } | undefined>)[path];
  expect(route?.options.beforeLoad).toBeTypeOf("function");
  return route!.options.beforeLoad!;
}

describe("route /agent/itsm/cis/$source/$ciId", () => {
  it("sends Tiqora CIs (Mode B) to the CMDB detail page", () => {
    const beforeLoad = beforeLoadOf("/agent/itsm/cis/$source/$ciId");
    let thrown: unknown;
    try {
      beforeLoad({ params: { source: "tiqora", ciId: "42" } });
    } catch (e) {
      thrown = e;
    }
    expect(isRedirect(thrown)).toBe(true);
    const options = (thrown as { options: { to: string; params: Record<string, string>; replace: boolean } }).options;
    expect(options.to).toBe("/agent/cmdb/$ciId");
    expect(options.params).toEqual({ ciId: "42" });
    expect(options.replace).toBe(true);
  });

  it("keeps Znuny CIs (Mode A) on the ITSM detail page", () => {
    const beforeLoad = beforeLoadOf("/agent/itsm/cis/$source/$ciId");
    expect(() => beforeLoad({ params: { source: "znuny", ciId: "201" } })).not.toThrow();
  });
});
