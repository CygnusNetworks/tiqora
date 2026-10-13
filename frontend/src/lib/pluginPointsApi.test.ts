import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { pluginPointsApi } from "./pluginPointsApi";

type Call = { method: string; path: string; body?: unknown };
let calls: Call[];

beforeEach(() => {
  calls = [];
  vi.spyOn(api, "request").mockImplementation((async (
    method: string,
    path: string,
    init?: { body?: unknown },
  ) => {
    calls.push({ method, path, body: init?.body });
    return [];
  }) as never);
});
afterEach(() => vi.restoreAllMocks());

describe("pluginPointsApi", () => {
  it("sends method, path and body each endpoint expects", async () => {
    await pluginPointsApi.numberGenerators();
    await pluginPointsApi.setNumberGenerator("Plugin_points_Yearly");
    await pluginPointsApi.setNumberGenerator(null);
    await pluginPointsApi.notificationTransports();
    await pluginPointsApi.dfPluginTypes();
    await pluginPointsApi.pluginChannels();
    await pluginPointsApi.externalCustomerImport("org.example.points:directory", "pp55-ext1");
    expect(calls).toEqual([
      { method: "GET", path: "/api/v1/admin/plugins/number-generators", body: undefined },
      { method: "PUT", path: "/api/v1/admin/plugins/number-generators", body: { key: "Plugin_points_Yearly" } },
      // null goes back to the Znuny scheme: the key is sent, not left out
      { method: "PUT", path: "/api/v1/admin/plugins/number-generators", body: { key: null } },
      { method: "GET", path: "/api/v1/admin/notification-events/transports", body: undefined },
      { method: "GET", path: "/api/v1/admin/dynamic-fields/plugin-types", body: undefined },
      { method: "GET", path: "/api/v1/channels/plugin", body: undefined },
      {
        method: "POST",
        path: "/api/v1/customers/external-import",
        body: { source: "org.example.points:directory", login: "pp55-ext1" },
      },
    ]);
  });
});
