import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";
import { DashboardConflictError, dashboardKeys, dashboardsApi, settingsHash } from "./dashboardsApi";

type Call = { method: string; url: string; body?: unknown; query?: Record<string, unknown> };
let calls: Call[];
let reply: () => Promise<unknown>;

beforeEach(() => {
  calls = [];
  reply = async () => ({});
  vi.spyOn(api, "request").mockImplementation((async (
    method: string,
    path: string,
    init?: { body?: unknown; query?: Record<string, unknown> },
  ) => {
    calls.push({ method, url: path, body: init?.body, query: init?.query });
    return reply();
  }) as never);
});
afterEach(() => vi.restoreAllMocks());

const lastCall = () => calls[calls.length - 1];
const lastUrl = () => lastCall().url;
const saveBody = { name: "Mein Start", icon: null, version: 3, widgets: [] };

describe("dashboardsApi", () => {
  it("save maps 409 to DashboardConflictError with the current version", async () => {
    reply = async () => {
      throw new ApiError(
        409,
        { detail: { message: "Dieses Dashboard wurde inzwischen geändert.", current_version: 7 } },
        "/api/v1/dashboards/1",
      );
    };
    const err = await dashboardsApi.save(1, saveBody).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DashboardConflictError);
    expect(err).toMatchObject({
      name: "DashboardConflictError",
      currentVersion: 7,
      message: "Dieses Dashboard wurde inzwischen geändert.",
    });
  });

  it("save passes other errors through unchanged", async () => {
    const boom = new ApiError(422, { detail: { message: "Zu hoch.", path: "widgets" } }, "/x");
    reply = async () => {
      throw boom;
    };
    await expect(dashboardsApi.save(1, saveBody)).rejects.toBe(boom);
    reply = async () => {
      throw new ApiError(409, "no version here", "/x");
    };
    await expect(dashboardsApi.save(1, saveBody)).rejects.toBeInstanceOf(ApiError);
  });

  it("data joins widget ids into one query parameter and omits it when none", async () => {
    await dashboardsApi.data(3, ["a", "b"]);
    expect(lastUrl()).toBe("/api/v1/dashboards/3/data?widgets=a,b");
    await dashboardsApi.data(3);
    expect(lastUrl()).toBe("/api/v1/dashboards/3/data");
    await dashboardsApi.data(3, []);
    expect(lastUrl()).toBe("/api/v1/dashboards/3/data");
  });

  it("encodes widget ids in the batch query and the per-widget paths", async () => {
    await dashboardsApi.data(3, ["a,b", "c&d"]);
    expect(lastUrl()).toBe("/api/v1/dashboards/3/data?widgets=a%2Cb,c%26d");
    await dashboardsApi.widgetData(3, "x/y");
    expect(lastUrl()).toBe("/api/v1/dashboards/3/widgets/x%2Fy/data");
  });

  it("previewData posts the settings to /dashboard-widget-types/{key}/data", async () => {
    await dashboardsApi.previewData("core.counter", { tone: "ok" });
    expect(lastCall()).toMatchObject({
      method: "POST",
      url: "/api/v1/dashboard-widget-types/core.counter/data",
      body: { settings: { tone: "ok" } },
    });
  });

  it("share sends group_ids; order sends ids; patch targets the widget settings route", async () => {
    await dashboardsApi.share(4, [2, 9]);
    expect(lastCall()).toMatchObject({ method: "PUT", url: "/api/v1/dashboards/4/share", body: { group_ids: [2, 9] } });
    await dashboardsApi.order([3, 1]);
    expect(lastCall()).toMatchObject({ method: "PUT", url: "/api/v1/dashboards/order", body: { ids: [3, 1] } });
    await dashboardsApi.patchWidgetSettings(4, "w1abcdefgh", { settings: { markdown: "x" }, version: 2 });
    expect(lastCall()).toMatchObject({
      method: "PATCH",
      url: "/api/v1/dashboards/4/widgets/w1abcdefgh/settings",
      body: { settings: { markdown: "x" }, version: 2 },
    });
  });

  it("template update sends the version as the lock query parameter", async () => {
    await dashboardsApi.templates.update(5, { name: "Support", role_ids: [1], locked: false, sort: 0 }, 4);
    expect(lastCall()).toMatchObject({
      method: "PUT",
      url: "/api/v1/admin/dashboard-templates/5",
      query: { version: 4 },
    });
  });

  it("template update maps 409 to DashboardConflictError", async () => {
    reply = async () => {
      throw new ApiError(409, { detail: { message: "Veraltet.", current_version: 9 } }, "/x");
    };
    await expect(
      dashboardsApi.templates.update(5, { name: "S", role_ids: [], locked: false, sort: 0 }, 4),
    ).rejects.toMatchObject({ name: "DashboardConflictError", currentVersion: 9 });
  });
});

describe("dashboardKeys / settingsHash", () => {
  it("nests every key under the shared prefix", () => {
    for (const key of [dashboardKeys.list(), dashboardKeys.one(1), dashboardKeys.data(1), dashboardKeys.widget(1, "w"), dashboardKeys.preview("k", "h")]) {
      expect(key[0]).toBe("dashboards");
    }
    expect(dashboardKeys.data(1)).not.toEqual(dashboardKeys.data(2));
  });

  it("hashes settings independent of key order", () => {
    expect(settingsHash({ a: 1, b: { c: 2, d: 3 } })).toBe(settingsHash({ b: { d: 3, c: 2 }, a: 1 }));
    expect(settingsHash({ a: 1 })).not.toBe(settingsHash({ a: 2 }));
  });
});

describe("dashboardsApi phase 2", () => {
  const filters = { filters: { queue_ids: [2], time_range: "7d" as const }, version: 3 };

  it("patchFilters PATCHes the filters route and maps 409 to DashboardConflictError", async () => {
    await dashboardsApi.patchFilters(4, filters);
    expect(lastCall()).toMatchObject({ method: "PATCH", url: "/api/v1/dashboards/4/filters", body: filters });
    reply = async () => {
      throw new ApiError(
        409,
        { detail: { message: "Dieses Dashboard wurde inzwischen geändert.", current_version: 9 } },
        "/api/v1/dashboards/4/filters",
      );
    };
    const err = await dashboardsApi.patchFilters(4, filters).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DashboardConflictError);
    expect(err).toMatchObject({ currentVersion: 9 });
  });

  it("templateUpdate reads and applyTemplateUpdate posts the action with the version", async () => {
    await dashboardsApi.templateUpdate(4);
    expect(lastCall()).toMatchObject({ method: "GET", url: "/api/v1/dashboards/4/template-update" });
    await dashboardsApi.applyTemplateUpdate(4, { action: "merge", version: 2 });
    expect(lastCall()).toMatchObject({
      method: "POST",
      url: "/api/v1/dashboards/4/template-update",
      body: { action: "merge", version: 2 },
    });
    reply = async () => {
      throw new ApiError(409, { detail: { message: "Stale.", current_version: 5 } }, "/x");
    };
    await expect(dashboardsApi.applyTemplateUpdate(4, { action: "dismiss", version: 2 })).rejects.toBeInstanceOf(
      DashboardConflictError,
    );
  });

  it("drillTickets encodes the widget id and passes paging, sort and point as query", async () => {
    await dashboardsApi.drillTickets(4, "w/1", { offset: 20, limit: 20, sort: "age", order: "desc", point: "Support" });
    expect(lastCall()).toMatchObject({
      method: "GET",
      url: "/api/v1/dashboards/4/widgets/w%2F1/tickets",
      query: { offset: 20, limit: 20, sort: "age", order: "desc", point: "Support" },
    });
  });

  it("startReportJob POSTs to the widget's report-job route", async () => {
    await dashboardsApi.startReportJob(4, "w-1");
    expect(lastCall()).toMatchObject({ method: "POST", url: "/api/v1/dashboards/4/widgets/w-1/report-job" });
  });

  it("kioskLinks list/create/remove hit the admin routes", async () => {
    await dashboardsApi.kioskLinks.list();
    expect(lastCall()).toMatchObject({ method: "GET", url: "/api/v1/admin/dashboard-kiosk-links" });
    const body = { name: "Lobby", dashboard_ids: [1], rotate_seconds: 60, expires_in_days: 90, show_details: false };
    await dashboardsApi.kioskLinks.create(body);
    expect(lastCall()).toMatchObject({ method: "POST", url: "/api/v1/admin/dashboard-kiosk-links", body });
    await dashboardsApi.kioskLinks.revoke(7);
    expect(lastCall()).toMatchObject({ method: "POST", url: "/api/v1/admin/dashboard-kiosk-links/7/revoke" });
    await dashboardsApi.kioskLinks.remove(7);
    expect(lastCall()).toMatchObject({ method: "DELETE", url: "/api/v1/admin/dashboard-kiosk-links/7" });
  });
});
