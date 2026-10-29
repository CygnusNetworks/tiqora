import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "@tiqora/api-client";

const json = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" }, ...init });

describe("ApiClient.requestWithHeaders (fake fetch)", () => {
  it("returns the parsed body together with the response headers and sends query and credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json([{ tn: "1" }], { headers: { "content-type": "application/json", "X-Schedule-Truncated": "true" } }));
    const client = new ApiClient({ baseUrl: "", fetch: fetchMock });
    const { data, headers } = await client.requestWithHeaders<{ tn: string }[]>("GET", "/api/x", { query: { start: "a", end: "b" } });
    expect(data).toEqual([{ tn: "1" }]);
    expect(headers.get("x-schedule-truncated")).toBe("true");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/x?start=a&end=b");
    expect(init).toMatchObject({ method: "GET", credentials: "include" });
  });

  it("returns undefined data and the headers for a 204", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204, headers: { "X-Foo": "bar" } }));
    const out = await new ApiClient({ baseUrl: "", fetch: fetchMock }).requestWithHeaders("DELETE", "/api/x");
    expect(out.data).toBeUndefined();
    expect(out.headers.get("x-foo")).toBe("bar");
  });

  it("throws ApiError with status and detail on an error and calls onUnauthorized for 401", async () => {
    const onUnauthorized = vi.fn();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ detail: { code: "nope" } }, { status: 404 }))
      .mockResolvedValueOnce(json({ detail: "auth" }, { status: 401 }));
    const client = new ApiClient({ baseUrl: "", fetch: fetchMock, onUnauthorized });
    await expect(client.requestWithHeaders("GET", "/api/x")).rejects.toMatchObject({ status: 404, detail: { detail: { code: "nope" } } });
    expect(onUnauthorized).not.toHaveBeenCalled();
    await expect(client.requestWithHeaders("GET", "/api/y")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("request still returns only the body", async () => {
    const client = new ApiClient({ baseUrl: "", fetch: vi.fn().mockResolvedValue(json({ a: 1 })) });
    await expect(client.request("GET", "/api/x")).resolves.toEqual({ a: 1 });
  });
});
