import { describe, expect, it } from "vitest";
import { createDemoDashboards } from "./dashboardsMock";
import { INVALID_KIOSK_TOKEN, KIOSK_SETTINGS_WHITELIST, MSG_HIDDEN, resolveKiosk } from "./kioskMock";

describe("demo kiosk", () => {
  it("answers every missing or invalid token with the same 404", () => {
    const demo = createDemoDashboards();
    for (const token of [null, undefined, "", INVALID_KIOSK_TOKEN]) {
      const out = resolveKiosk(demo, "GET", "/api/v1/kiosk/board", token);
      expect(out?.status).toBe(404);
      expect(out?.body).toMatchObject({ detail: { message: expect.any(String) } });
    }
  });

  it("ignores foreign paths and non-GET methods", () => {
    const demo = createDemoDashboards();
    expect(resolveKiosk(demo, "GET", "/api/v1/dashboards", "t")).toBeUndefined();
    expect(resolveKiosk(demo, "POST", "/api/v1/kiosk/board", "t")).toBeUndefined();
  });

  it("serves a board whose settings never leave the whitelist", () => {
    const demo = createDemoDashboards({
      extraDashboards: [
        {
          name: "Geheim",
          widgets: [
            { id: "s1", type_key: "core.ticket-list", type_version: 1, title_override: "Privat", settings: { source: { kind: "filter", where: { field: "customer_user_id" } }, limit: 5, columns: ["tn"] }, layout: { lg: { x: 0, y: 0, w: 6, h: 4 } }, pinned: false },
            { id: "s2", type_key: "core.notes", type_version: 1, title_override: null, settings: { markdown: "intern" }, layout: { lg: { x: 6, y: 0, w: 6, h: 4 } }, pinned: false },
          ],
        },
      ],
    });
    const out = resolveKiosk(demo, "GET", "/api/v1/kiosk/board", "tok");
    expect(out?.status).toBe(200);
    const board = out?.body as { dashboards: { widgets: { type_key: string; settings: Record<string, unknown>; title_override?: unknown }[] }[] };
    for (const d of board.dashboards) {
      for (const w of d.widgets) {
        const allowed = KIOSK_SETTINGS_WHITELIST[w.type_key] ?? [];
        expect(Object.keys(w.settings).filter((k) => !allowed.includes(k))).toEqual([]);
        expect(w).not.toHaveProperty("title_override");
      }
    }
    expect(JSON.stringify(board)).not.toContain("customer_user_id");
    expect(JSON.stringify(board)).not.toContain("intern");
  });

  it("hides widgets without a wallboard rule and serves the rest", () => {
    const demo = createDemoDashboards();
    const d = demo.ensureStart();
    const out = resolveKiosk(demo, "GET", `/api/v1/kiosk/dashboards/${d.id}/data`, "tok");
    const data = out?.body as { widgets: Record<string, { status: string; error?: string }> };
    expect(data.widgets["demo-w-open"].status).toBe("ok");
    expect(data.widgets["demo-w-recent"]).toEqual({ status: "forbidden", error: MSG_HIDDEN });
    expect(resolveKiosk(demo, "GET", "/api/v1/kiosk/dashboards/999/data", "tok")?.status).toBe(404);
  });
});
