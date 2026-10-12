import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRotation } from "./useRotation";

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  setHidden(false);
});
afterEach(() => {
  setHidden(false);
  vi.useRealTimers();
});

describe("useRotation", () => {
  it("advances every N seconds and wraps around", () => {
    const { result } = renderHook(() => useRotation(3, 15));
    expect(result.current.index).toBe(0);
    act(() => void vi.advanceTimersByTime(14_999));
    expect(result.current.index).toBe(0);
    act(() => void vi.advanceTimersByTime(1));
    expect(result.current.index).toBe(1);
    act(() => void vi.advanceTimersByTime(15_000));
    expect(result.current.index).toBe(2);
    act(() => void vi.advanceTimersByTime(15_000));
    expect(result.current.index).toBe(0);
  });

  it("pauses while the page is hidden and goes on when it is back", () => {
    const { result } = renderHook(() => useRotation(2, 15));
    act(() => setHidden(true));
    act(() => void vi.advanceTimersByTime(60_000));
    expect(result.current.index).toBe(0);
    act(() => setHidden(false));
    act(() => void vi.advanceTimersByTime(15_000));
    expect(result.current.index).toBe(1);
  });

  it("starts no timer for one dashboard (or none)", () => {
    const { result } = renderHook(() => useRotation(1, 15));
    expect(vi.getTimerCount()).toBe(0);
    act(() => void vi.advanceTimersByTime(120_000));
    expect(result.current.index).toBe(0);
    const none = renderHook(() => useRotation(0, 15));
    expect(vi.getTimerCount()).toBe(0);
    expect(none.result.current.index).toBe(0);
  });

  it("stands still while paused", () => {
    const { result, rerender } = renderHook(({ paused }) => useRotation(2, 15, { paused }), { initialProps: { paused: true } });
    act(() => void vi.advanceTimersByTime(60_000));
    expect(result.current.index).toBe(0);
    rerender({ paused: false });
    act(() => void vi.advanceTimersByTime(15_000));
    expect(result.current.index).toBe(1);
  });

  it("next and prev wrap and restart the countdown; a shrinking list keeps the index valid", () => {
    const { result, rerender } = renderHook(({ count }) => useRotation(count, 15), { initialProps: { count: 3 } });
    act(() => result.current.prev());
    expect(result.current.index).toBe(2);
    act(() => void vi.advanceTimersByTime(10_000));
    act(() => result.current.next());
    expect(result.current.index).toBe(0);
    act(() => void vi.advanceTimersByTime(14_000));
    expect(result.current.index).toBe(0);
    act(() => void vi.advanceTimersByTime(1_000));
    expect(result.current.index).toBe(1);
    rerender({ count: 1 });
    expect(result.current.index).toBe(0);
  });
});
