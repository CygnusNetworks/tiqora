import { describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { ADMIN_PAGES, rankAdminPages } from "./adminSearch";

describe("ADMIN_PAGES", () => {
  it("has unique slugs and routes", () => {
    expect(new Set(ADMIN_PAGES.map((p) => p.slug)).size).toBe(ADMIN_PAGES.length);
    expect(new Set(ADMIN_PAGES.map((p) => p.route)).size).toBe(ADMIN_PAGES.length);
  });

  it("contains the dashboard templates page once, with a route, group and German keywords", () => {
    const p = ADMIN_PAGES.filter((x) => x.slug === "dashboard-templates");
    expect(p).toHaveLength(1);
    expect(p[0].route).toBe("/admin/dashboard-templates");
    expect(p[0].group).toBe("system");
    expect(p[0].keywords).toEqual(expect.arrayContaining(["startseite", "vorlage", "dashboard", "widgets", "rolle"]));
  });

  it("finds the page by its German keywords in the command palette", async () => {
    await i18n.changeLanguage("de");
    const t = (key: string) => i18n.t(key);
    for (const query of ["Startseite", "Vorlage", "Rolle"]) {
      expect(rankAdminPages(ADMIN_PAGES, query, t).map((p) => p.slug), query).toContain("dashboard-templates");
    }
  });
});
