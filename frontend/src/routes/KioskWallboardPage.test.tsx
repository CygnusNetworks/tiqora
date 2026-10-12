import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { DASHBOARD_WIDGETS } from "@/plugins/dashboardWidgets";
import { registerCoreWidgets } from "@/dashboard/widgets";
import type { KioskBoard } from "@/lib/kioskApi";
import { readKioskToken } from "@/dashboard/wallboard/kioskToken";
import { KioskWallboardPage } from "./KioskWallboardPage";

const TOKEN = "s3cret-wallboard-token";
const rect = (x: number, y: number, w: number, h: number) => ({ lg: { x, y, w, h } });

function board(widgets: KioskBoard["dashboards"][number]["widgets"], extra: Partial<KioskBoard> = {}): KioskBoard {
  return {
    name: "TV",
    rotate_seconds: 60,
    refresh_seconds: 30,
    show_details: false,
    server_time: "2026-10-12T08:00:00Z",
    dashboards: [{ id: 7, name: "Leitstand", widgets }],
    ...extra,
  };
}

const COUNTER = { id: "c1", type_key: "core.counter", type_version: 1, layout: rect(0, 0, 3, 2), settings: { tone: "neutral" } };
const RECENT = { id: "r1", type_key: "core.recent-views", type_version: 1, layout: rect(3, 0, 3, 2), settings: {} };
const PLUGIN = { id: "p1", type_key: "acme.thing", type_version: 1, layout: rect(6, 0, 3, 2), settings: {} };
const REPORT = { id: "rep1", type_key: "core.report", type_version: 1, layout: rect(0, 2, 6, 3), settings: { display: "auto", max_rows: 10 } };

type Handler = (path: string, init: RequestInit) => Response | Promise<Response>;
let fetchMock: ReturnType<typeof vi.fn>;
let locationAssign: ReturnType<typeof vi.fn>;

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

function serve(handler: Handler) {
  fetchMock.mockImplementation((url: string, init: RequestInit) => {
    if (!String(url).startsWith("/api/v1/kiosk/")) return Promise.resolve(json(404, {}));
    return Promise.resolve(handler(String(url), init));
  });
}

function kioskCalls(): string[] {
  return fetchMock.mock.calls.map((c) => String(c[0])).filter((u) => u.startsWith("/api/v1/kiosk/"));
}

async function mount(hash: string | null = `#${TOKEN}`) {
  window.history.replaceState(null, "", `/wallboard${hash ?? ""}`);
  const rootRoute = createRootRoute();
  const route = createRoute({ getParentRoute: () => rootRoute, path: "/wallboard", component: KioskWallboardPage });
  const router = createRouter({
    routeTree: rootRoute.addChildren([route]),
    history: createMemoryHistory({ initialEntries: ["/wallboard"] }),
  });
  await act(async () => {
    await router.load();
  });
  return render(
    <I18nextProvider i18n={i18n}>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      <RouterProvider router={router as any} />
    </I18nextProvider>,
  );
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
beforeEach(async () => {
  DASHBOARD_WIDGETS.clearForTests();
  registerCoreWidgets();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  locationAssign = vi.fn();
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
  const real = window.location;
  vi.spyOn(window, "location", "get").mockImplementation(
    () => new Proxy(real, { get: (t, k) => (k === "assign" ? locationAssign : Reflect.get(t, k) === undefined ? undefined : typeof Reflect.get(t, k) === "function" ? Reflect.get(t, k).bind(t) : Reflect.get(t, k)) }),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  DASHBOARD_WIDGETS.clearForTests();
  document.head.querySelectorAll('meta[name="referrer"]').forEach((m) => m.remove());
});

describe("readKioskToken", () => {
  it("reads the fragment and treats an empty one as no token", () => {
    expect(readKioskToken("#abc-DEF_1")).toBe("abc-DEF_1");
    expect(readKioskToken("#a%20b")).toBe("a b");
    expect(readKioskToken("")).toBeNull();
    expect(readKioskToken("#")).toBeNull();
  });
});

describe("KioskWallboardPage", () => {
  it("reads the token from the hash and never puts it into a request URL", async () => {
    serve((path) =>
      path.endsWith("/board")
        ? json(200, board([COUNTER]))
        : json(200, { generated_at: "2026-10-12T08:00:01Z", widgets: { c1: { status: "ok", data: { value: 42, delta: null, spark: [] } } } }),
    );
    await mount();
    await waitFor(() => expect(screen.getByTestId("counter-value")).toHaveTextContent("42"));
    const calls = fetchMock.mock.calls.filter((c) => String(c[0]).startsWith("/api/v1/kiosk/")) as [string, RequestInit][];
    expect(calls.map((c) => c[0])).toEqual(expect.arrayContaining(["/api/v1/kiosk/board", "/api/v1/kiosk/dashboards/7/data"]));
    for (const [url, init] of calls) {
      expect(url).not.toContain(TOKEN);
      expect(new Headers(init.headers).get("X-Tiqora-Kiosk")).toBe(TOKEN);
      expect(init.credentials).toBe("omit");
    }
    // nothing else (no /me-style call of the page itself, no authenticated API)
    expect(fetchMock.mock.calls.every((c) => !String(c[0]).includes(TOKEN))).toBe(true);
  });

  it("sets no-referrer while mounted and removes it afterwards", async () => {
    serve(() => json(200, board([])));
    const view = await mount();
    expect(document.head.querySelector('meta[name="referrer"]')?.getAttribute("content")).toBe("no-referrer");
    view.unmount();
    expect(document.head.querySelector('meta[name="referrer"]')).toBeNull();
  });

  it("invalid link shows the message and stops polling", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve(() => json(404, { detail: { message: "Wallboard-Link nicht gefunden." } }));
    await mount();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Dieser Wallboard-Link ist ungültig oder abgelaufen."));
    const before = kioskCalls().length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20 * 60_000);
    });
    expect(kioskCalls().length).toBe(before);
    expect(locationAssign).not.toHaveBeenCalled();
  });

  it("a link without a token is invalid and calls nothing", async () => {
    serve(() => json(200, board([])));
    await mount(null);
    expect(screen.getByRole("alert")).toHaveTextContent("Dieser Wallboard-Link ist ungültig oder abgelaufen.");
    expect(kioskCalls()).toEqual([]);
  });

  it("429/503 keep the last picture, show the stale banner and retry with backoff", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let dataCalls = 0;
    serve((path) => {
      if (path.endsWith("/board")) return json(200, board([COUNTER]));
      dataCalls += 1;
      if (dataCalls === 1) return json(200, { generated_at: "2026-10-12T08:00:01Z", widgets: { c1: { status: "ok", data: { value: 17, delta: null, spark: [] } } } });
      if (dataCalls <= 3) return json(dataCalls === 2 ? 429 : 503, { detail: { message: "später" } }, dataCalls === 2 ? { "Retry-After": "1" } : {});
      return json(200, { generated_at: "2026-10-12T08:05:00Z", widgets: { c1: { status: "ok", data: { value: 18, delta: null, spark: [] } } } });
    });
    await mount();
    await waitFor(() => expect(screen.getByTestId("counter-value")).toHaveTextContent("17"));
    expect(screen.getByTestId("wallboard-status")).not.toHaveAttribute("data-stale");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000); // refresh -> 429
    });
    expect(dataCalls).toBe(2);
    expect(screen.getByTestId("wallboard-status")).toHaveAttribute("data-stale", "true");
    expect(screen.getByTestId("counter-value")).toHaveTextContent("17");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(14_000); // backoff floor is 15 s: not yet
    });
    expect(dataCalls).toBe(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000); // -> 503, the next pause doubles to 30 s
    });
    expect(dataCalls).toBe(3);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(27_000);
    });
    expect(dataCalls).toBe(3);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000); // recovered
    });
    expect(dataCalls).toBe(4);
    await waitFor(() => expect(screen.getByTestId("counter-value")).toHaveTextContent("18"));
    expect(screen.getByTestId("wallboard-status")).not.toHaveAttribute("data-stale");
    expect(locationAssign).not.toHaveBeenCalled();
  });

  it("a network failure at the start shows loading, not a login redirect", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await mount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(screen.getByTestId("kiosk-notice")).toHaveTextContent("Wird geladen");
    expect(locationAssign).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe("/wallboard");
  });

  it("never navigates to /login on an unexpected status", async () => {
    serve((path) => (path.endsWith("/board") ? json(200, board([COUNTER])) : json(401, { detail: "Not authenticated" })));
    await mount();
    await waitFor(() => expect(screen.getByTestId("wallboard")).toBeInTheDocument());
    await act(async () => {
      await Promise.resolve();
    });
    expect(locationAssign).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe("/wallboard");
  });

  it("plugin and recent-views widgets render 'Auf dem Wallboard nicht verfügbar'", async () => {
    serve((path) =>
      path.endsWith("/board")
        ? json(200, board([COUNTER, RECENT, PLUGIN]))
        : json(200, { generated_at: "2026-10-12T08:00:01Z", widgets: { c1: { status: "ok", data: { value: 1, delta: null, spark: [] } }, p1: { status: "forbidden", error: "Auf dem Wallboard ausgeblendet." } } }),
    );
    await mount();
    await waitFor(() => expect(screen.getByTestId("counter-value")).toBeInTheDocument());
    const tiles = screen.getAllByTestId("widget-kiosk-unavailable");
    expect(tiles).toHaveLength(2);
    for (const tile of tiles) expect(tile).toHaveTextContent("Auf dem Wallboard nicht verfügbar");
    expect(screen.queryByTestId("recent-views-widget")).toBeNull();
  });

  it("a deferred report renders 'Dieser Bericht ist für das Wallboard zu umfangreich.'", async () => {
    serve((path) =>
      path.endsWith("/board")
        ? json(200, board([REPORT]))
        : json(200, { generated_at: "2026-10-12T08:00:01Z", widgets: { rep1: { status: "ok", data: { deferred: true, kiosk: true, report: { key: "tickets-by-queue", name: "Tickets je Queue" } } } } }),
    );
    await mount();
    await waitFor(() => expect(screen.getByText("Dieser Bericht ist für das Wallboard zu umfangreich.")).toBeInTheDocument());
    // a kiosk never starts a background job
    expect(kioskCalls().every((u) => u.startsWith("/api/v1/kiosk/"))).toBe(true);
    expect(fetchMock.mock.calls.every((c) => String(c[0]).startsWith("/api/v1/kiosk/"))).toBe(true);
  });

  it("no element is a link on the kiosk", async () => {
    serve((path) =>
      path.endsWith("/board")
        ? json(200, board([COUNTER, { id: "q1", type_key: "core.queue-overview", type_version: 1, layout: rect(3, 0, 5, 3), settings: {} }, { id: "t1", type_key: "core.ticket-list", type_version: 1, layout: rect(0, 3, 8, 4), settings: {} }]))
        : json(200, {
            generated_at: "2026-10-12T08:00:01Z",
            widgets: {
              c1: { status: "ok", data: { value: 5, delta: 1, spark: [1, 2] } },
              q1: { status: "ok", data: { items: [{ queue_id: 3, name: "Support", open: 4, escalated: 1 }] } },
              t1: { status: "ok", data: { items: [{ id: 99, tn: "2026101200001", title: "Drucker", queue: "Support", state: "open" }], total: 1 } },
            },
          }),
    );
    await mount();
    await waitFor(() => expect(screen.getByTestId("counter-value")).toHaveTextContent("5"));
    const page = screen.getByTestId("kiosk-page");
    expect(page.querySelectorAll("a, [href]")).toHaveLength(0);
  });
});
