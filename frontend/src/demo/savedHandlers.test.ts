import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { handlers } from "./handlers";

const server = setupServer(...handlers);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

const DEF = { schema_version: 1, source: "tickets", dimensions: [], measures: [{ key: "tickets" }] };
const send = (method: string, path: string, body?: unknown) =>
  fetch(`http://localhost${path}`, { method, headers: { "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined });

describe("demo MSW handlers for saved reports and searches", () => {
  it("saves a report from the request body and lists, runs and deletes it", async () => {
    const res = await send("POST", "/api/v1/reports", { name: "Via MSW", description: "", definition: DEF, visibility: "private", group_ids: [] });
    expect(res.status).toBe(201);
    const made = (await res.json()) as { id: number; name: string };
    expect(made.name).toBe("Via MSW");
    const cat = (await (await send("GET", "/api/v1/reports")).json()) as { reports: { key: string }[] };
    expect(cat.reports.some((r) => r.key === String(made.id))).toBe(true);
    expect((await send("DELETE", `/api/v1/reports/${made.id}`)).status).toBe(204);
  });

  it("queries tickets with offset and limit from the body", async () => {
    const page = (await (await send("POST", "/api/v1/tickets/query", { filter: { version: 1 }, offset: 0, limit: 2 })).json()) as {
      items: unknown[];
      total: number;
    };
    expect(page.items).toHaveLength(2);
    expect(page.total).toBeGreaterThan(2);
  });

  it("serves a YAML file download", async () => {
    const res = await send("GET", "/api/v1/reports/builtin.volume/file?format=yaml");
    expect(res.headers.get("content-disposition")).toContain(".yaml");
    expect(await res.text()).toContain("format: tiqora-report");
  });

  it("still answers the report export through the catch-all", async () => {
    expect(await (await send("POST", "/api/v1/reports/export", { report_key: "builtin.volume" })).json()).toMatchObject({ job_id: 9201 });
  });
});
