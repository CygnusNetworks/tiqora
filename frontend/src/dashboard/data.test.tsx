import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { dashboardKeys, dashboardsApi, type DashboardData, type WidgetResult } from "@/lib/dashboardsApi";
import { handleSSEMessage } from "@/lib/useSSE";
import { DASHBOARD_WIDGETS } from "@/plugins/dashboardWidgets";
import { PENDING_RETRY_DELAYS_MS, useDashboardData, usePreviewData } from "./data";
import type { EditWidget } from "./model";
import { registerCoreWidgets } from "./widgets";

function wire(id: string, type_key: string): EditWidget {
  return {
    id,
    type_key,
    type_version: 1,
    title_override: null,
    settings: {},
    layout: { lg: { x: 0, y: 0, w: 3, h: 2 } },
  };
}

const ok = (value = 1): WidgetResult => ({ status: "ok", data: { value, delta: null, spark: [] } });
const pending = (): WidgetResult => ({ status: "pending" });

// counter: ticket events; escalations: ticket_changed + interval:60; notes: no endpoint; one unknown type
const WIDGETS = [
  wire("w-counter", "core.counter"),
  wire("w-esc", "core.escalations"),
  wire("w-notes", "core.notes"),
  wire("w-gone", "org.example.gone"),
];

let client: QueryClient;
let data: ReturnType<typeof vi.spyOn>;
let widgetData: ReturnType<typeof vi.spyOn>;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { value: hidden, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

const emit = (message: object) => handleSSEMessage(client, JSON.stringify(message));

beforeEach(async () => {
  await i18n.changeLanguage("de");
  registerCoreWidgets();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  data = vi.spyOn(dashboardsApi, "data").mockResolvedValue({
    widgets: { "w-counter": ok(), "w-esc": { status: "ok", data: { items: [], total: 0, oldest_epoch: null } } },
  });
  widgetData = vi.spyOn(dashboardsApi, "widgetData").mockResolvedValue(ok(9));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  Object.defineProperty(document, "hidden", { value: false, configurable: true });
  client.clear();
});

describe("useDashboardData", () => {
  it("issues one batch request and maps every widget to a state", async () => {
    const { result } = renderHook(() => useDashboardData(1, WIDGETS, { enabled: true }), { wrapper });
    expect(result.current.byWidget["w-counter"].status).toBe("loading");
    expect(result.current.byWidget["w-notes"].status).toBe("none");
    await waitFor(() => expect(result.current.byWidget["w-counter"].status).toBe("ok"));
    expect(result.current.byWidget["w-counter"].data).toEqual({ value: 1, delta: null, spark: [] });
    expect(result.current.byWidget["w-notes"].status).toBe("none");
    expect(result.current.byWidget["w-gone"].status).toBe("none");
    expect(data).toHaveBeenCalledTimes(1);
    expect(data).toHaveBeenCalledWith(1, undefined, expect.anything());
    expect(widgetData).not.toHaveBeenCalled();
    expect(client.getQueryData(dashboardKeys.data(1))).toBeDefined();
  });

  it("makes no request while disabled and keeps the cached data", async () => {
    client.setQueryData<DashboardData>(dashboardKeys.data(1), { widgets: { "w-counter": ok(5) } });
    const { result } = renderHook(() => useDashboardData(1, WIDGETS, { enabled: false }), { wrapper });
    expect(result.current.byWidget["w-counter"]).toMatchObject({ status: "ok", data: { value: 5 } });
    emit({ type: "ticket_changed", ticket_id: 1, event: "x" });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(data).not.toHaveBeenCalled();
    expect(widgetData).not.toHaveBeenCalled();
  });

  it("a failed batch turns every data widget into an error tile; a tile's retry fetches the whole batch again", async () => {
    data.mockRejectedValueOnce(new Error("boom"));
    const { result } = renderHook(() => useDashboardData(1, WIDGETS, { enabled: true }), { wrapper });
    await waitFor(() => expect(result.current.byWidget["w-counter"].status).toBe("error"));
    expect(result.current.byWidget["w-esc"].status).toBe("error");
    expect(result.current.byWidget["w-notes"].status).toBe("none");

    // still failing: every tile is an error tile again (none turns into a false empty state)
    let fail: (e: Error) => void = () => {};
    data.mockImplementationOnce(() => new Promise<DashboardData>((_r, reject) => (fail = reject)));
    act(() => result.current.refetch("w-counter"));
    await waitFor(() => expect(result.current.byWidget["w-esc"].status).toBe("loading"));
    expect(result.current.byWidget["w-counter"].status).toBe("loading");
    act(() => fail(new Error("still down")));
    await waitFor(() => expect(result.current.byWidget["w-counter"].status).toBe("error"));
    expect(result.current.byWidget["w-esc"].status).toBe("error");
    expect(client.getQueryData(dashboardKeys.data(1))).toBeUndefined();

    act(() => result.current.refetch("w-esc"));
    await waitFor(() => expect(result.current.byWidget["w-counter"]).toMatchObject({ status: "ok", data: { value: 1 } }));
    expect(result.current.byWidget["w-esc"].status).toBe("ok");
    expect(data).toHaveBeenCalledTimes(3);
    for (const call of data.mock.calls) expect(call[1]).toBeUndefined();
    expect(widgetData).not.toHaveBeenCalled();
  });

  it("a tile answered 'error' inside a successful batch retries only that widget", async () => {
    data.mockResolvedValueOnce({ widgets: { "w-counter": { status: "error", error: "Kaputt." }, "w-esc": ok(2) } });
    const { result } = renderHook(() => useDashboardData(1, WIDGETS, { enabled: true }), { wrapper });
    await waitFor(() => expect(result.current.byWidget["w-counter"]).toMatchObject({ status: "error", error: "Kaputt." }));
    act(() => result.current.refetch("w-counter"));
    expect(result.current.byWidget["w-counter"].status).toBe("loading");
    await waitFor(() => expect(result.current.byWidget["w-counter"]).toMatchObject({ status: "ok", data: { value: 9 } }));
    expect(result.current.byWidget["w-esc"]).toMatchObject({ status: "ok", data: { value: 2 } });
    expect(widgetData).toHaveBeenCalledTimes(1);
    expect(widgetData).toHaveBeenCalledWith(1, "w-counter", expect.anything());
    expect(data).toHaveBeenCalledTimes(1);
  });

  it("an SSE refresh during a slow first batch creates no partial entry: the other tiles keep loading", async () => {
    vi.useFakeTimers();
    let release: (v: DashboardData) => void = () => {};
    data.mockImplementationOnce(() => new Promise<DashboardData>((r) => (release = r)));
    data.mockResolvedValue({ widgets: { "w-counter": ok(7), "w-esc": ok(8) } });
    const { result } = renderHook(() => useDashboardData(1, WIDGETS, { enabled: true }), { wrapper });
    emit({ type: "ticket_new_in_queue", ticket_id: 3, tn: "1", title: "x", queue_id: 1, queue_name: "Q" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(data).toHaveBeenCalledTimes(2); // the SSE batch ran ...
    expect(client.getQueryData(dashboardKeys.data(1))).toBeUndefined(); // ... but wrote nothing yet
    expect(result.current.byWidget["w-counter"].status).toBe("loading");
    expect(result.current.byWidget["w-esc"].status).toBe("loading");
    await act(async () => {
      release({ widgets: { "w-counter": ok(1), "w-esc": ok(2) } });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.byWidget["w-counter"]).toMatchObject({ status: "ok", data: { value: 1 } });
    expect(result.current.byWidget["w-esc"]).toMatchObject({ status: "ok", data: { value: 2 } });
  });

  it("polls a pending widget with growing pauses, gives up with a message, and never polls while hidden", async () => {
    vi.useFakeTimers();
    // like the server: a refresh of some ids answers only those
    data.mockImplementation(async (_id: number, ids?: string[]) => {
      const all: Record<string, WidgetResult> = { "w-counter": pending(), "w-esc": ok() };
      return { widgets: Object.fromEntries(Object.entries(all).filter(([k]) => !ids || ids.includes(k))) };
    });
    widgetData.mockResolvedValue(pending());
    const { result } = renderHook(() => useDashboardData(1, WIDGETS, { enabled: true }), { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.byWidget["w-counter"].status).toBe("pending");
    expect(PENDING_RETRY_DELAYS_MS[0]).toBe(2000);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1999);
    });
    expect(widgetData).toHaveBeenCalledTimes(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(widgetData).toHaveBeenCalledTimes(1);
    expect(widgetData).toHaveBeenLastCalledWith(1, "w-counter", expect.anything());

    // hidden page: the next poll is held back
    setHidden(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(widgetData).toHaveBeenCalledTimes(1);
    setHidden(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(widgetData).toHaveBeenCalledTimes(2);

    const rest = PENDING_RETRY_DELAYS_MS.slice(2).reduce((a, b) => a + b, 0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(rest + 1000);
    });
    expect(widgetData).toHaveBeenCalledTimes(PENDING_RETRY_DELAYS_MS.length);
    expect(result.current.byWidget["w-counter"]).toMatchObject({ status: "error", error: "Das dauert zu lange." });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300_000);
    });
    expect(widgetData).toHaveBeenCalledTimes(PENDING_RETRY_DELAYS_MS.length);
    expect(result.current.byWidget["w-esc"].status).toBe("ok");
  });

  it("a polled widget that becomes ready is merged into the cache", async () => {
    vi.useFakeTimers();
    data.mockResolvedValue({ widgets: { "w-counter": pending(), "w-esc": ok() } });
    widgetData.mockResolvedValue(ok(4));
    const { result } = renderHook(() => useDashboardData(1, WIDGETS, { enabled: true }), { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.byWidget["w-counter"]).toMatchObject({ status: "ok", data: { value: 4 } });
    expect(client.getQueryData<DashboardData>(dashboardKeys.data(1))?.widgets["w-counter"]).toEqual(ok(4));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(widgetData).toHaveBeenCalledTimes(1);
  });

  it("SSE events are coalesced for 2 s and batches keep 10 s apart (5 events in 30 s -> at most 3 batches)", async () => {
    vi.useFakeTimers();
    renderHook(() => useDashboardData(1, WIDGETS, { enabled: true }), { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(data).toHaveBeenCalledTimes(1);
    for (let i = 0; i < 5; i++) {
      emit({ type: "ticket_changed", ticket_id: i, event: "x" });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(6000);
      });
    }
    const sseBatches = data.mock.calls.slice(1);
    expect(sseBatches.length).toBeGreaterThanOrEqual(1);
    expect(sseBatches.length).toBeLessThanOrEqual(3);
    for (const call of sseBatches) {
      expect([...(call[1] as string[])].sort()).toEqual(["w-counter", "w-esc"]);
      expect(call[2]).toBeDefined();
    }
  });

  it("SSE refresh merges into the cache entry and only asks for widgets listing the event", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useDashboardData(1, WIDGETS, { enabled: true }), { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    data.mockResolvedValueOnce({ widgets: { "w-counter": ok(7) } });
    emit({ type: "ticket_new_in_queue", ticket_id: 3, tn: "1", title: "x", queue_id: 1, queue_name: "Q" });
    emit({ type: "presence_changed", ticket_id: 3 });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(data).toHaveBeenCalledTimes(2);
    expect(data).toHaveBeenLastCalledWith(1, ["w-counter"], expect.anything());
    expect(result.current.byWidget["w-counter"]).toMatchObject({ status: "ok", data: { value: 7 } });
    expect(result.current.byWidget["w-esc"].status).toBe("ok");
  });

  it("no SSE refresh while hidden; one refresh when the page becomes visible again", async () => {
    vi.useFakeTimers();
    renderHook(() => useDashboardData(1, WIDGETS, { enabled: true }), { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    setHidden(true);
    emit({ type: "ticket_changed", ticket_id: 1, event: "x" });
    emit({ type: "ticket_changed", ticket_id: 2, event: "x" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(data).toHaveBeenCalledTimes(1);
    setHidden(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(data).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    // the interval:60 widget ticked meanwhile at most once; no further SSE batch
    expect(data.mock.calls.filter((c: unknown[]) => Array.isArray(c[1]) && (c[1] as string[]).includes("w-counter"))).toHaveLength(1);
  });

  it("interval widgets refetch on their own timer, paused while hidden, cleared on unmount", async () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useDashboardData(1, WIDGETS, { enabled: true }), { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(data).toHaveBeenCalledTimes(2);
    expect(data).toHaveBeenLastCalledWith(1, ["w-esc"], expect.anything());
    setHidden(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(data).toHaveBeenCalledTimes(2);
    setHidden(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    // the ticks skipped while hidden become one refresh after the page is visible again
    expect(data).toHaveBeenCalledTimes(3);
    expect(data).toHaveBeenLastCalledWith(1, ["w-esc"], expect.anything());
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(data).toHaveBeenCalledTimes(3);
  });
});

describe("usePreviewData", () => {
  it("debounces settings and fetches the preview once per settled value", async () => {
    vi.useFakeTimers();
    const preview = vi.spyOn(dashboardsApi, "previewData").mockResolvedValue(ok(3));
    const { result, rerender } = renderHook(
      ({ limit }: { limit: number }) => usePreviewData("core.counter", { limit }, { debounceMs: 300 }),
      { wrapper, initialProps: { limit: 1 } },
    );
    expect(result.current.status).toBe("loading");
    rerender({ limit: 2 });
    rerender({ limit: 3 });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(preview).toHaveBeenCalledTimes(1);
    expect(preview).toHaveBeenCalledWith("core.counter", { limit: 3 }, expect.anything());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current).toMatchObject({ status: "ok", data: { value: 3 } });
    // an equal object (new identity) does not refetch
    rerender({ limit: 3 });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(preview).toHaveBeenCalledTimes(1);
  });

  it("is idle ('none') for a type without data and when disabled", () => {
    const preview = vi.spyOn(dashboardsApi, "previewData");
    const notes = renderHook(() => usePreviewData("core.notes", {}), { wrapper });
    expect(notes.result.current.status).toBe("none");
    const off = renderHook(() => usePreviewData("core.counter", {}, { enabled: false }), { wrapper });
    expect(off.result.current.status).toBe("none");
    expect(preview).not.toHaveBeenCalled();
  });
});

// The registry is module state shared with other test files in this worker.
afterEach(() => {
  if (!DASHBOARD_WIDGETS.get("core.counter")) registerCoreWidgets();
});
