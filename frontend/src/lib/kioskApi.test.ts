import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { KioskLinkInvalidError, KioskRetryableError, kioskApi } from "./kioskApi";

let fetchMock: ReturnType<typeof vi.fn>;

function reply(status: number, body: unknown, headers: Record<string, string> = {}) {
  fetchMock.mockResolvedValueOnce(
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } }),
  );
}

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("kioskApi", () => {
  it("sends the secret in X-Tiqora-Kiosk, omits credentials and sends no Authorization", async () => {
    reply(200, { name: "TV" });
    await kioskApi.board("secret-token");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/kiosk/board");
    expect(init.credentials).toBe("omit");
    expect(init.method).toBe("GET");
    const headers = new Headers(init.headers);
    expect(headers.get("X-Tiqora-Kiosk")).toBe("secret-token");
    expect(headers.has("Authorization")).toBe(false);
    expect(url).not.toContain("secret-token");
  });

  it("reads one dashboard's data from the dashboard path", async () => {
    reply(200, { generated_at: "2026-10-12T08:00:00Z", widgets: {} });
    const out = await kioskApi.data("tok", 42);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/kiosk/dashboards/42/data");
    expect(out.widgets).toEqual({});
  });

  it("maps 404 to KioskLinkInvalidError with the server message", async () => {
    reply(404, { detail: { message: "Wallboard-Link nicht gefunden." } });
    const err = await kioskApi.board("bad").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(KioskLinkInvalidError);
    expect((err as Error).message).toBe("Wallboard-Link nicht gefunden.");
  });

  it("maps 429 and 503 to a retryable error that honours Retry-After", async () => {
    reply(429, { detail: { message: "Zu viele Anfragen." } }, { "Retry-After": "17" });
    const limited = await kioskApi.board("t").catch((e: unknown) => e);
    expect(limited).toBeInstanceOf(KioskRetryableError);
    expect(limited).toMatchObject({ status: 429, retryAfterSeconds: 17 });

    reply(503, { detail: { message: "Der Dienst ist vorübergehend nicht verfügbar." } }, { "Retry-After": "30" });
    const down = await kioskApi.data("t", 1).catch((e: unknown) => e);
    expect(down).toBeInstanceOf(KioskRetryableError);
    expect(down).toMatchObject({ status: 503, retryAfterSeconds: 30 });
  });

  it("falls back to a default delay without a usable Retry-After", async () => {
    reply(503, { detail: { message: "x" } }, { "Retry-After": "soon" });
    const err = (await kioskApi.board("t").catch((e: unknown) => e)) as KioskRetryableError;
    expect(err.retryAfterSeconds).toBe(30);
  });

  it("turns other failures into ApiError and network failures into a retryable error", async () => {
    reply(500, { detail: "boom" });
    await expect(kioskApi.board("t")).rejects.toBeInstanceOf(ApiError);
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const err = await kioskApi.board("t").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(KioskRetryableError);
    expect(err).toMatchObject({ status: 0 });
  });

  it("never swallows an abort", async () => {
    const abort = new DOMException("aborted", "AbortError");
    fetchMock.mockRejectedValueOnce(abort);
    await expect(kioskApi.board("t")).rejects.toBe(abort);
  });
});
