import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { KioskLinkInvalidError, kioskApi, type KioskBoard, type KioskData } from "@/lib/kioskApi";
import { backoffSeconds, mergeKioskData, useKioskBoard, useKioskData } from "./useKioskData";

const BOARD: KioskBoard = {
  name: "TV",
  rotate_seconds: 60,
  refresh_seconds: 30,
  show_details: false,
  server_time: "2026-10-12T08:00:00Z",
  dashboards: [],
};
const dataOf = (value: number): KioskData => ({
  generated_at: "2026-10-12T08:00:00Z",
  widgets: { w: { status: "ok", data: { value } } },
});

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("backoffSeconds", () => {
  it("doubles from 15 s up to 5 minutes and honours a longer Retry-After", () => {
    expect([1, 2, 3, 4, 5, 6, 9].map((n) => backoffSeconds(n))).toEqual([15, 30, 60, 120, 240, 300, 300]);
    expect(backoffSeconds(1, 90)).toBe(90);
    expect(backoffSeconds(1, 9999)).toBe(300);
  });
});

describe("mergeKioskData", () => {
  it("keeps the last good value of a widget that is pending now", () => {
    const first = mergeKioskData(undefined, dataOf(5));
    const next = mergeKioskData(first, { generated_at: "x", widgets: { w: { status: "pending" }, v: { status: "pending" } } });
    expect(next.widgets.w).toEqual({ status: "ok", data: { value: 5 }, error: undefined });
    expect(next.widgets.v.status).toBe("pending");
  });

  it("takes errors and fresh values as they come", () => {
    const first = mergeKioskData(undefined, dataOf(5));
    expect(mergeKioskData(first, dataOf(6)).widgets.w.data).toEqual({ value: 6 });
    expect(mergeKioskData(first, { generated_at: "x", widgets: { w: { status: "error", error: "kaputt" } } }).widgets.w).toMatchObject({
      status: "error",
      error: "kaputt",
    });
  });
});

describe("useKioskBoard", () => {
  it("reads the board once per 10 minutes and ends on an invalid link", async () => {
    const board = vi.spyOn(kioskApi, "board").mockResolvedValue(BOARD);
    const { result } = renderHook(() => useKioskBoard("tok"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.board).toBe(BOARD);
    expect(board).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9 * 60_000);
    });
    expect(board).toHaveBeenCalledTimes(1);
    board.mockRejectedValueOnce(new KioskLinkInvalidError("weg"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000);
    });
    expect(result.current.invalid).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30 * 60_000);
    });
    expect(board).toHaveBeenCalledTimes(2);
  });
});

describe("useKioskData", () => {
  it("polls the current dashboard every refresh_seconds and prefetches the next 5 s before the rotation", async () => {
    const data = vi.spyOn(kioskApi, "data").mockImplementation(async (_t, id) => dataOf(id));
    const { result } = renderHook(() => useKioskData("tok", BOARD, 1, 2));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(data.mock.calls.map((c) => c[1])).toEqual([1]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(data.mock.calls.map((c) => c[1])).toEqual([1, 1]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(24_000); // t = 54 s: not yet
    });
    expect(data.mock.calls.some((c) => c[1] === 2)).toBe(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000); // t = 56 s: 5 s before the 60 s rotation (and the 60 s poll of 1 is still ahead)
    });
    expect(data.mock.calls.filter((c) => c[1] === 2)).toHaveLength(1);
    expect(result.current.byDashboard[2].widgets.w.data).toEqual({ value: 2 });
  });

  it("does not fetch a just-prefetched dashboard again the moment the rotation switches to it", async () => {
    const data = vi.spyOn(kioskApi, "data").mockImplementation(async (_t, id) => dataOf(id));
    const { rerender } = renderHook(({ cur, next }) => useKioskData("tok", BOARD, cur, next), { initialProps: { cur: 1, next: 2 } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(56_000);
    });
    const callsFor2 = () => data.mock.calls.filter((c) => c[1] === 2).length;
    expect(callsFor2()).toBe(1);
    rerender({ cur: 2, next: 1 });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(callsFor2()).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(callsFor2()).toBe(2);
  });

  it("a 404 marks the link invalid and stops the polling", async () => {
    const data = vi.spyOn(kioskApi, "data").mockRejectedValue(new KioskLinkInvalidError("weg"));
    const { result } = renderHook(() => useKioskData("tok", BOARD, 1, null));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.invalid).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000);
    });
    expect(data).toHaveBeenCalledTimes(1);
  });
});
