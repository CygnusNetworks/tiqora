import { afterEach, describe, expect, it } from "vitest";
import type { CiPage, ImpactGraph, LinkOut } from "@/lib/cmdbApi";
import type { PortalCiPage } from "@/lib/portalCmdbApi";
import { createDemoCmdb, demoCmdbJob, demoCmdbReport, resolveCmdb } from "./cmdbMock";
import { DEMO_ITSM_MODE_KEY } from "./itsmMode";

afterEach(() => localStorage.removeItem(DEMO_ITSM_MODE_KEY));

const get = (store: ReturnType<typeof createDemoCmdb>, path: string, q = "") =>
  resolveCmdb(store, "GET", path, undefined, new URLSearchParams(q));

describe("cmdbMock", () => {
  it("has two classes and six CIs", () => {
    const store = createDemoCmdb();
    const classes = get(store, "/api/v1/cmdb/classes")?.body as unknown[];
    expect(classes).toHaveLength(2);
    const page = resolveCmdb(store, "POST", "/api/v1/cmdb/cis/search", { limit: 50 })?.body as CiPage;
    expect(page.total).toBe(6);
    expect(page.items.map((i) => i.name)).toContain("web-01.example.org");
  });

  it("answers /cmdb/status from the demo ITSM mode", () => {
    const store = createDemoCmdb();
    expect((get(store, "/api/v1/cmdb/status")?.body as { mode: string }).mode).toBe("tiqora");
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "znuny_readthrough");
    expect((get(store, "/api/v1/cmdb/status")?.body as { mode: string }).mode).toBe("znuny_readthrough");
  });

  it("shows the incident chain and one link the caller may not see", () => {
    const store = createDemoCmdb();
    const page = resolveCmdb(store, "POST", "/api/v1/cmdb/cis/search", { q: "db-01" })?.body as CiPage;
    expect(page.items).toHaveLength(1);
    expect(page.items[0].incident_functionality).toBe("incident");
    const web = resolveCmdb(store, "POST", "/api/v1/cmdb/cis/search", { q: "web-01" })?.body as CiPage;
    expect(web.items[0].incident_functionality).toBe("warning");
    const links = get(store, "/api/v1/cmdb/cis/3/links")?.body as LinkOut[];
    expect(links.filter((l) => l.other.hidden)).toHaveLength(1);
    expect(links.find((l) => l.other.hidden)?.other.label).toBe("Kein Zugriff");
    const graph = get(store, "/api/v1/cmdb/cis/3/impact")?.body as ImpactGraph;
    expect(graph.nodes.filter((n) => n.hidden)).toHaveLength(1);
    expect(graph.nodes.find((n) => n.id === "ci:1")?.incident_functionality).toBe("warning");
  });

  it("creates, updates and deletes a CI in the in-memory store", () => {
    const store = createDemoCmdb();
    const created = resolveCmdb(store, "POST", "/api/v1/cmdb/cis", {
      class_id: 1, name: "app-01.example.org", deployment_state_id: 2, incident_state_id: 11,
    });
    expect(created?.status).toBe(201);
    const id = (created?.body as { id: number }).id;
    resolveCmdb(store, "PUT", `/api/v1/cmdb/cis/${id}`, { name: "app-02.example.org" });
    expect((get(store, `/api/v1/cmdb/cis/${id}`)?.body as { name: string }).name).toBe("app-02.example.org");
    expect(resolveCmdb(store, "DELETE", `/api/v1/cmdb/cis/${id}`)?.status).toBe(204);
    expect(get(store, `/api/v1/cmdb/cis/${id}`)?.status).toBe(404);
  });

  it("serves the portal only portal-visible classes and portal attributes", () => {
    const store = createDemoCmdb();
    const page = get(store, "/api/portal/config-items")?.body as PortalCiPage;
    expect(page.items.map((i) => i.class_name)).toEqual(["Server", "Server", "Server", "Server"]);
    const detail = get(store, "/api/portal/config-items/1")?.body as { rendered: { key: string; portal: boolean }[] };
    expect(detail.rendered.map((r) => r.key)).toEqual(["hostname", "ip"]);
    expect(get(store, "/api/portal/config-items/5")?.status).toBe(404);
  });

  it("runs import jobs: a dry run reports a faulty row, 'abort' fails the real run, 'skip' writes the rest", () => {
    const store = createDemoCmdb();
    const start = (dry_run: boolean, on_error: string) => {
      const form = new FormData();
      form.set("file", new File(["a"], "a.csv"));
      form.set("request", JSON.stringify({ dry_run, options: { on_error } }));
      return (resolveCmdb(store, "POST", "/api/v1/cmdb/import-jobs", form)?.body as { job_id: number }).job_id;
    };
    const dry = demoCmdbJob(store, start(true, "abort"));
    expect(dry).toMatchObject({ status: "succeeded", result: { errors: 1, created: 3, dry_run: true } });
    expect(demoCmdbReport(store, dry!.id)).toContain("5;error;;");
    const aborted = demoCmdbJob(store, start(false, "abort"));
    expect(aborted).toMatchObject({ status: "failed", result: null });
    expect(aborted?.error).toContain("nichts geschrieben");
    expect(aborted?.files.map((f) => f.name)).toContain("report.csv");
    const skipped = demoCmdbJob(store, start(false, "skip"));
    expect(skipped).toMatchObject({ status: "succeeded", result: { errors: 0, skipped: 1, dry_run: false } });
  });

  it("stores import templates per class", () => {
    const store = createDemoCmdb();
    const body = { name: "Alt", class_id: 1, file_format: "csv", mapping: [{ column: "A", target: "name" }], options: { identifiers: ["name"] }, shared: false };
    const created = resolveCmdb(store, "POST", "/api/v1/cmdb/import-templates", body);
    expect(created?.status).toBe(201);
    expect((get(store, "/api/v1/cmdb/import-templates", "class_id=1")?.body as unknown[]).length).toBe(1);
    expect((get(store, "/api/v1/cmdb/import-templates", "class_id=2")?.body as unknown[]).length).toBe(0);
  });

  it("returns undefined for unknown routes", () => {
    expect(get(createDemoCmdb(), "/api/v1/other")).toBeUndefined();
  });
});
