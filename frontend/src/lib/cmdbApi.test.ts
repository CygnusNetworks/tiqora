import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { cmdbApi } from "./cmdbApi";
import { cmdbAdminApi } from "./cmdbAdminApi";
import { portalApi } from "./portalApi";
import { portalCmdbApi } from "./portalCmdbApi";

afterEach(() => vi.restoreAllMocks());

describe("cmdbApi", () => {
  it("posts the search body and returns the page", async () => {
    const page = { items: [], total: 0, total_capped: false };
    const spy = vi.spyOn(api, "request").mockResolvedValue(page);
    const out = await cmdbApi.search({ class_ids: [1], q: "web", sort: "number", order: "asc", offset: 0, limit: 50 });
    expect(out).toEqual(page);
    expect(spy).toHaveBeenCalledWith(
      "POST",
      "/api/v1/cmdb/cis/search",
      expect.objectContaining({ body: expect.objectContaining({ q: "web", class_ids: [1] }) }),
    );
  });

  it("uploads import files as multipart with the request JSON", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({ job_id: 5, status: "queued" });
    const file = new File(["a;b\n1;2\n"], "cis.csv", { type: "text/csv" });
    await cmdbApi.startImport(file, { class_id: 1, dry_run: true });
    const [method, path, init] = spy.mock.calls[0];
    expect([method, path]).toEqual(["POST", "/api/v1/cmdb/import-jobs"]);
    const form = (init as { body: FormData }).body;
    expect(form).toBeInstanceOf(FormData);
    expect((form.get("file") as File).name).toBe("cis.csv");
    expect(JSON.parse(String(form.get("request")))).toEqual({ class_id: 1, dry_run: true });
  });

  it("uploads an attachment with the CI id and builds download URLs", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({});
    await cmdbApi.uploadAttachment(new File(["x"], "a.txt"), 7);
    const form = (spy.mock.calls[0][2] as { body: FormData }).body;
    expect(form.get("ci_id")).toBe("7");
    expect(cmdbApi.attachmentUrl(3)).toBe("/api/v1/cmdb/attachments/3");
  });

  it("passes list and query parameters through", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue([]);
    await cmdbApi.lookup("web", ["server", "site"], 10);
    await cmdbApi.diff(4, 41, 42);
    await cmdbApi.tickets(4, true);
    expect(spy).toHaveBeenNthCalledWith(1, "GET", "/api/v1/cmdb/cis/lookup", {
      query: { q: "web", class_keys: ["server", "site"], limit: 10 },
    });
    expect(spy).toHaveBeenNthCalledWith(2, "GET", "/api/v1/cmdb/cis/4/diff", {
      query: { from_version: 41, to_version: 42 },
    });
    expect(spy).toHaveBeenNthCalledWith(3, "GET", "/api/v1/cmdb/cis/4/tickets", { query: { open_only: true } });
  });

  it("sends a link with the single LinkIn shape", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({});
    const body = { source_kind: "ci", source_key: "1", target_kind: "ci", target_key: "3", link_type: "DependsOn" } as const;
    await cmdbApi.addLink(body);
    expect(spy).toHaveBeenCalledWith("POST", "/api/v1/cmdb/links", { body });
  });
});

describe("cmdbAdminApi and portalCmdbApi", () => {
  it("talks to the admin and portal prefixes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({});
    await cmdbAdminApi.recomputePropagation();
    expect(spy).toHaveBeenCalledWith("POST", "/api/v1/admin/cmdb/propagation/recompute");
    const portalSpy = vi.spyOn(portalApi, "request").mockResolvedValue({});
    await portalCmdbApi.get(9);
    expect(portalSpy).toHaveBeenCalledWith("GET", "/api/portal/config-items/9");
  });
});
