import { describe, expect, it } from "vitest";
import { router } from "@/router";

describe("route /admin/dashboard-kiosk-links", () => {
  it("is registered under the admin layout, which is the RequireAdmin gate", () => {
    const route = (router.routesByPath as Record<string, { parentRoute?: { id: string } } | undefined>)["/admin/dashboard-kiosk-links"];
    expect(route).toBeDefined();
    expect(route?.parentRoute?.id).toBe("/admin");
  });
});
