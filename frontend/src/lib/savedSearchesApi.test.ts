import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { savedSearchesApi } from "./savedSearchesApi";

afterEach(() => vi.restoreAllMocks());

it("calls the saved-search, ticket-query and import routes", async () => {
  const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
  await savedSearchesApi.list();
  await savedSearchesApi.counts();
  await savedSearchesApi.setPrefs(5, { show_in_sidebar: true, show_count: false });
  await savedSearchesApi.queryTickets({ filter: { version: 1 } });
  await savedSearchesApi.znunyImport(["A"]);
  await savedSearchesApi.znunyPreview();
  await savedSearchesApi.remove(5);
  await savedSearchesApi.countTickets({ version: 1 });
  await savedSearchesApi.ticketFilterFields();
  expect(spy.mock.calls.map((c) => [c[0], c[1]])).toEqual([
    ["GET", "/api/v1/saved-searches"],
    ["GET", "/api/v1/saved-searches/counts"],
    ["PUT", "/api/v1/saved-searches/5/prefs"],
    ["POST", "/api/v1/tickets/query"],
    ["POST", "/api/v1/saved-searches/import/znuny"],
    ["GET", "/api/v1/saved-searches/import/znuny"],
    ["DELETE", "/api/v1/saved-searches/5"],
    ["POST", "/api/v1/ticket-filters/count"],
    ["GET", "/api/v1/ticket-filters/fields"],
  ]);
  expect(spy.mock.calls[2][2]).toMatchObject({ body: { show_in_sidebar: true, show_count: false } });
  expect(spy.mock.calls[4][2]).toMatchObject({ body: { names: ["A"] } });
});
