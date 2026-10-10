import { describe, expect, it } from "vitest";
import {
  createDemoDashboards,
  demoDashboard,
  demoDefaultWidgets,
  demoTab,
  demoTeamDashboard,
  demoTemplate,
  demoWidgetData,
  resolve,
  resolveDashboards,
} from "./dashboardsMock";
import { resolveData } from "./mockData";

describe("demo dashboards store", () => {
  it("is stateful: save bumps the version, create adds a tab, ensureStart returns the default", async () => {
    const demo = createDemoDashboards();
    const d = await demo.ensureStart();
    expect(d.widgets).toHaveLength(11);
    const saved = await demo.save(d.id, { name: "X", icon: null, version: d.version, widgets: d.widgets.slice(0, 2) });
    expect(saved.version).toBe(d.version + 1);
    expect(saved.widgets).toHaveLength(2);
    await demo.create({ name: "Zweites" });
    expect((await demo.list()).map((t) => t.name)).toEqual(["X", "Zweites"]);
  });

  it("answers a stale save with 409 and the current version", () => {
    const demo = createDemoDashboards();
    const d = demo.ensureStart();
    demo.save(d.id, { name: d.name, icon: null, version: d.version, widgets: d.widgets });
    const out = resolveDashboards(demo, "PUT", `/api/v1/dashboards/${d.id}`, { name: "Alt", icon: null, version: d.version, widgets: [] });
    expect(out?.status).toBe(409);
    expect(out?.body).toMatchObject({ detail: { current_version: d.version + 1 } });
  });

  it("returns what was sent on save, patch, create, share and delete (no empty bodies)", () => {
    const demo = createDemoDashboards();
    const d = demo.ensureStart();
    const put = resolveDashboards(demo, "PUT", `/api/v1/dashboards/${d.id}`, {
      name: "Umbenannt",
      icon: "star",
      version: d.version,
      widgets: [d.widgets[0]],
    });
    expect(put?.body).toMatchObject({ name: "Umbenannt", icon: "star", version: d.version + 1 });

    const wid = d.widgets[0].id;
    const patch = resolveDashboards(demo, "PATCH", `/api/v1/dashboards/${d.id}/widgets/${wid}/settings`, {
      settings: { tone: "ok", source: { kind: "preset", preset: "mine_new" } },
      version: d.version + 1,
    });
    expect(patch?.body).toMatchObject({ id: wid, settings: { tone: "ok" } });

    const created = resolveDashboards(demo, "POST", "/api/v1/dashboards", { name: "Kopie", copy_from: d.id });
    expect(created?.status).toBe(201);
    expect(created?.body).toMatchObject({ name: "Kopie" });
    const copy = created?.body as { id: number; widgets: { id: string }[] };
    expect(copy.widgets.map((w) => w.id)).not.toContain(wid);

    const shared = resolveDashboards(demo, "PUT", `/api/v1/dashboards/${copy.id}/share`, { group_ids: [3] });
    expect(shared?.body).toMatchObject({ kind: "shared", group_ids: [3] });

    expect(resolveDashboards(demo, "DELETE", `/api/v1/dashboards/${copy.id}`)?.status).toBe(204);
    expect(resolveDashboards(demo, "GET", `/api/v1/dashboards/${copy.id}`)?.status).toBe(404);
  });

  it("orders tabs, and keeps the last dashboard", () => {
    const demo = createDemoDashboards({ extraTabs: ["Alt"] });
    const [first, second] = demo.list();
    resolveDashboards(demo, "PUT", "/api/v1/dashboards/order", { ids: [second.id, first.id] });
    expect(demo.list().map((t) => t.name)).toEqual(["Alt", "Mein Start"]);
    demo.remove(first.id);
    expect(resolveDashboards(demo, "DELETE", `/api/v1/dashboards/${second.id}`)?.status).toBe(422);
  });

  it("serves batched data for all or the requested widgets, and omits widgets without a server side", () => {
    const demo = createDemoDashboards();
    const d = demo.ensureStart();
    const all = resolveDashboards(demo, "GET", `/api/v1/dashboards/${d.id}/data`)?.body as { widgets: Record<string, unknown> };
    // recent-views and queue-overview are frontend-only
    expect(Object.keys(all.widgets)).toHaveLength(9);
    const some = resolveDashboards(demo, "GET", `/api/v1/dashboards/${d.id}/data`, undefined, new URLSearchParams("widgets=demo-w-open,demo-w-recent"))?.body as { widgets: Record<string, unknown> };
    expect(Object.keys(some.widgets)).toEqual(["demo-w-open"]);
    expect(resolveDashboards(demo, "GET", `/api/v1/dashboards/${d.id}/widgets/demo-w-recent/data`)?.status).toBe(404);
  });

  it("previews unsaved settings through the widget type route", () => {
    const demo = createDemoDashboards();
    const out = resolveDashboards(demo, "POST", "/api/v1/dashboard-widget-types/core.ticket-list/data", {
      settings: { source: { kind: "preset", preset: "unowned_new" }, limit: 2 },
    });
    expect(out?.body).toMatchObject({ status: "ok", data: { total: 4 } });
    expect((out?.body as { data: { items: unknown[] } }).data.items).toHaveLength(2);
    expect(resolveDashboards(demo, "POST", "/api/v1/dashboard-widget-types/core.notes/data", { settings: {} })?.status).toBe(404);
  });

  it("manages admin templates with the optimistic lock", () => {
    const demo = createDemoDashboards();
    const [seed] = demo.templates.list();
    const body = { name: "Vertrieb", role_ids: [4], locked: true, sort: 1 };
    const created = resolveDashboards(demo, "POST", "/api/v1/admin/dashboard-templates", body);
    expect(created?.status).toBe(201);
    expect(created?.body).toMatchObject({ name: "Vertrieb", locked: true, role_ids: [4] });
    const updated = resolveDashboards(demo, "PUT", `/api/v1/admin/dashboard-templates/${seed.id}`, { ...body, name: "Neu" }, new URLSearchParams(`version=${seed.version}`));
    expect(updated?.body).toMatchObject({ name: "Neu", version: seed.version + 1 });
    const stale = resolveDashboards(demo, "PUT", `/api/v1/admin/dashboard-templates/${seed.id}`, body, new URLSearchParams(`version=${seed.version}`));
    expect(stale?.status).toBe(409);
    expect(resolveDashboards(demo, "DELETE", `/api/v1/admin/dashboard-templates/${seed.id}`)?.status).toBe(204);
    expect(demo.templates.list().map((t) => t.name)).toEqual(["Vertrieb"]);
  });

  it("ignores foreign paths and lists the ten core widget types", () => {
    expect(resolve("GET", "/api/v1/saved-searches")).toBeUndefined();
    const types = resolve("GET", "/api/v1/dashboard-widget-types")?.body as { key: string }[];
    expect(types).toHaveLength(10);
    expect(types.map((t) => t.key)).toContain("core.counter");
  });

  it("is reachable through the shared demo resolver", () => {
    expect(resolveData("/api/v1/dashboards", "GET")).toEqual(expect.arrayContaining([expect.objectContaining({ name: "Mein Start" })]));
  });
});

describe("typed factories", () => {
  it("build complete rows with overrides", () => {
    expect(demoTab({ name: "Z" })).toMatchObject({ name: "Z", kind: "personal", version: 1, group_ids: [] });
    expect(demoDashboard({ widgets: demoDefaultWidgets() }).widgets).toHaveLength(11);
    expect(demoTemplate({ locked: true })).toMatchObject({ kind: "template", locked: true, role_ids: [], widget_count: 0 });
  });
});

describe("demo widget data", () => {
  it("returns plausible shapes for counter, ticket-list, escalations, reminders, calendar, team", () => {
    expect(demoWidgetData("core.counter", { source: { kind: "preset", preset: "mine_open" } })).toMatchObject({
      status: "ok",
      data: { value: 12, delta: 2, spark: expect.any(Array) },
    });
    expect(demoWidgetData("core.ticket-list", { limit: 3 })).toMatchObject({ status: "ok", data: { total: 6, items: [{}, {}, {}] } });
    const esc = demoWidgetData("core.escalations", { within_hours: 0, limit: 8 })?.data as {
      items: { epoch: number; kind: string }[];
      oldest_epoch: number;
    };
    expect(esc.items.map((e) => e.kind)).toEqual(["solution", "update", "first_response", "escalation"]);
    expect(esc.oldest_epoch).toBe(esc.items[0].epoch);
    expect(esc.items[0].epoch).toBeLessThan(Date.now() / 1000);
    const near = demoWidgetData("core.escalations", { within_hours: 1 })?.data as { items: unknown[] };
    expect(near.items).toHaveLength(3); // deadlines past or within the hour; the 2h10m one is out
    expect(demoWidgetData("core.reminders", { window_hours: 24 })?.data).toMatchObject({ total: 3 });
    expect(demoWidgetData("core.calendar-agenda", {})?.data).toMatchObject({ items: [{ title: "Team-Daily" }, {}, {}] });
    expect(demoWidgetData("core.team-presence", {})?.data).toMatchObject({ total_online: 3 });
  });

  it("has no data for frontend-only and unknown types", () => {
    for (const key of ["core.recent-views", "core.queue-overview", "core.notes", "core.links", "plugin.x"]) {
      expect(demoWidgetData(key, {})).toBeUndefined();
    }
  });
});

describe("public demo store", () => {
  it("lists 'Mein Start' and the shared 'Team Support' with the demo plugin's widget", () => {
    const list = resolve("GET", "/api/v1/dashboards")?.body as { id: number; name: string; kind: string; owner_name: string | null }[];
    expect(list.map((t) => t.name)).toEqual(["Mein Start", "Team Support"]);
    expect(list[1]).toMatchObject({ kind: "shared", owner_name: "Maya Rivera" });
    const team = resolve("GET", `/api/v1/dashboards/${list[1].id}`)?.body as { widgets: { type_key: string }[] };
    expect(team.widgets.map((w) => w.type_key)).toContain("org.example.demo.hello");
    expect(demoTeamDashboard().widgets).toHaveLength(6);
    // the e2e fixture store is unchanged by it
    expect(createDemoDashboards({ extraTabs: ["Alt"] }).list().map((t) => t.name)).toEqual(["Mein Start", "Alt"]);
  });
});
