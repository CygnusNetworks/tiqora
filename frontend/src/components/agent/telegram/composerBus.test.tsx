import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { requestComposer, useComposerRequests } from "./composerBus";

describe("composerBus", () => {
  it("delivers a request only to subscribers of the same ticket", () => {
    const seven = vi.fn();
    const eight = vi.fn();
    renderHook(() => useComposerRequests(7, seven));
    renderHook(() => useComposerRequests(8, eight));

    requestComposer(7, { quoteArticleId: 42, focus: true });

    expect(seven).toHaveBeenCalledWith({ quoteArticleId: 42, focus: true });
    expect(eight).not.toHaveBeenCalled();
  });

  it("stops delivering after unmount", () => {
    const handler = vi.fn();
    const { unmount } = renderHook(() => useComposerRequests(9, handler));
    unmount();
    requestComposer(9, { focus: true });
    expect(handler).not.toHaveBeenCalled();
  });

  it("calls the latest handler without resubscribing", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ h }) => useComposerRequests(10, h), {
      initialProps: { h: first },
    });
    rerender({ h: second });
    requestComposer(10, { quoteArticleId: 1 });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith({ quoteArticleId: 1 });
  });

  it("is a no-op without subscribers", () => {
    expect(() => requestComposer(11, { focus: true })).not.toThrow();
  });
});
