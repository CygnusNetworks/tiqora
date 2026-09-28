import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import {
  requestComposer,
  requestConversationView,
  useComposerRequests,
  useConversationViewRequests,
} from "./composerBus";

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

  it("does not throw without subscribers — the request is buffered, not dropped (see the buffering tests below)", () => {
    expect(() => requestComposer(11, { focus: true })).not.toThrow();
  });

  it("buffers a request until a subscriber appears, then delivers it once", () => {
    const handler = vi.fn();
    requestComposer(20, { focus: true });
    expect(handler).not.toHaveBeenCalled();

    renderHook(() => useComposerRequests(20, handler));
    expect(handler).toHaveBeenCalledWith({ focus: true });

    requestComposer(20, { quoteArticleId: 1 });
    expect(handler).toHaveBeenCalledTimes(2);
  });

  it("does not replay a buffered request to a second, later subscriber", () => {
    const first = vi.fn();
    const second = vi.fn();
    requestComposer(21, { focus: true });
    renderHook(() => useComposerRequests(21, first));
    renderHook(() => useComposerRequests(21, second));
    expect(first).toHaveBeenCalledWith({ focus: true });
    expect(second).not.toHaveBeenCalled();
  });

  it("passes an AI draft through untouched", () => {
    const handler = vi.fn();
    renderHook(() => useComposerRequests(22, handler));
    requestComposer(22, { draft: { id: 5, body: "Danke!" }, focus: true });
    expect(handler).toHaveBeenCalledWith({ draft: { id: 5, body: "Danke!" }, focus: true });
  });
});

describe("composerBus view-switch requests", () => {
  it("delivers a conversation-view request only to subscribers of the same ticket", () => {
    const thirty = vi.fn();
    const thirtyOne = vi.fn();
    renderHook(() => useConversationViewRequests(30, thirty));
    renderHook(() => useConversationViewRequests(31, thirtyOne));

    requestConversationView(30);

    expect(thirty).toHaveBeenCalledOnce();
    expect(thirtyOne).not.toHaveBeenCalled();
  });

  it("is a no-op without subscribers — unlike the composer bus, this one never buffers (the article view is always mounted)", () => {
    expect(() => requestConversationView(32)).not.toThrow();
  });

  it("stops delivering after unmount", () => {
    const handler = vi.fn();
    const { unmount } = renderHook(() => useConversationViewRequests(33, handler));
    unmount();
    requestConversationView(33);
    expect(handler).not.toHaveBeenCalled();
  });
});
