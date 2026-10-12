import { describe, expect, it } from "vitest";
import { resolveData } from "./mockData";

const BASE = "/api/v1/admin/process-designer";

describe("demo process designer and deploy status", () => {
  it("deploy status carries znuny_sync and deploy_mode per kind (DeployStatusCard reads both)", () => {
    const status = resolveData("/api/v1/admin/deploy-status", "GET") as Record<
      "process" | "acl",
      { znuny_sync: { state: string }; deploy_mode: string | null }
    >;
    for (const kind of ["process", "acl"] as const) {
      expect(status[kind].znuny_sync.state).toEqual(expect.any(String));
      expect(status[kind]).toHaveProperty("deploy_mode");
    }
  });

  it("answers designer writes with a draft or an entity, never {}", () => {
    const saved = resolveData(`${BASE}/processes/Process-demo`, "PUT") as { process: { name: string } };
    expect(saved.process.name).toBe("Bestellung");
    const a = resolveData(`${BASE}/entities/Activity`, "POST") as { entity_id: string; entity_type: string };
    const b = resolveData(`${BASE}/entities/Activity`, "POST") as { entity_id: string };
    expect(a.entity_type).toBe("Activity");
    expect(a.entity_id).not.toBe(b.entity_id);
    const one = resolveData(`${BASE}/entities/Transition/Transition-1`, "GET") as { name: string; config: object };
    expect(one.name).toBe("Antrag vollständig");
    expect(resolveData(`${BASE}/entities/Transition/Transition-1`, "PUT")).toMatchObject({ entity_id: "Transition-1" });
  });

  it("lists entities with their users, including one no process uses", () => {
    const rows = resolveData(`${BASE}/entities/Activity`, "GET") as { entity_id: string; used_by: string[] }[];
    expect(rows.find((r) => r.entity_id === "Activity-spare")?.used_by).toEqual([]);
    expect(rows.find((r) => r.entity_id === "Activity-1")?.used_by).toEqual(["Process-demo"]);
  });
});
