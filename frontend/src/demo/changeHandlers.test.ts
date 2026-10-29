import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { handlers } from "./handlers";
import { DEMO_ITSM_MODE_KEY } from "./itsmMode";

const server = setupServer(...handlers);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

const get = (path: string) => fetch(`http://localhost${path}`);

describe("demo MSW handler for /api/v1/itsm/*", () => {
  it("still answers the ITSM mock's null (recent-incident) with 200, not 404", async () => {
    const res = await get("/api/v1/itsm/services/1/recent-incident");
    expect(res.status).toBe(200);
    expect(await res.json()).toBeNull();
  });

  it("answers the change routes after the ITSM mock", async () => {
    const res = await get("/api/v1/itsm/changes?phase=approval");
    expect(res.status).toBe(200);
    expect(((await res.json()) as { total: number }).total).toBe(1);
  });

  it("answers 409 change_mode_a for the change routes in Mode A", async () => {
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "znuny_readthrough");
    try {
      const res = await get("/api/v1/itsm/changes");
      expect(res.status).toBe(409);
      expect(await res.json()).toEqual({ detail: { code: "change_mode_a" } });
      expect((await get("/api/v1/itsm/changes/status")).status).toBe(200);
      expect((await get("/api/v1/itsm/legacy-changes")).status).toBe(200);
    } finally {
      localStorage.removeItem(DEMO_ITSM_MODE_KEY);
    }
  });
});
