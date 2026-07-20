import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { handleSSEMessage, presenceQueryKey, useSSE } from "./useSSE";
import { clearNotifications, useNotifications } from "./notificationStore";

describe("handleSSEMessage", () => {
  it("invalidates the tickets cache prefix for ticket_changed", () => {
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    handleSSEMessage(
      queryClient,
      JSON.stringify({ type: "ticket_changed", ticket_id: 42, event: "TicketCreate" }),
    );

    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ queryKey: ["tickets"] });
  });

  it("invalidates the ticket-scoped presence cache for presence_changed", () => {
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    handleSSEMessage(queryClient, JSON.stringify({ type: "presence_changed", ticket_id: 7 }));

    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ queryKey: presenceQueryKey(7) });
  });

  it("ignores malformed payloads without throwing", () => {
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    expect(() => handleSSEMessage(queryClient, "not json")).not.toThrow();
    expect(spy).not.toHaveBeenCalled();
  });

  it("ignores unknown message types", () => {
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    handleSSEMessage(queryClient, JSON.stringify({ type: "something_else" }));

    expect(spy).not.toHaveBeenCalled();
  });

  it("stores a notification and refreshes tickets+queues for ticket_new_in_queue", () => {
    clearNotifications();
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    handleSSEMessage(
      queryClient,
      JSON.stringify({
        type: "ticket_new_in_queue",
        ticket_id: 5,
        tn: "T5",
        title: "New mail",
        queue_id: 2,
        queue_name: "Raw",
      }),
    );

    expect(spy).toHaveBeenCalledWith({ queryKey: ["tickets"] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["queues"] });

    const { result } = renderHook(() => useNotifications());
    expect(result.current.unreadCount).toBe(1);
    expect(result.current.items[0]).toMatchObject({ ticketId: 5, tn: "T5", queueName: "Raw" });
    clearNotifications();
  });
});

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  withCredentials: boolean;
  listeners: Record<string, ((evt: MessageEvent<string>) => void)[]> = {};
  closed = false;

  constructor(url: string, options?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = options?.withCredentials ?? false;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (evt: MessageEvent<string>) => void) {
    this.listeners[type] = [...(this.listeners[type] ?? []), listener];
  }

  removeEventListener(type: string, listener: (evt: MessageEvent<string>) => void) {
    this.listeners[type] = (this.listeners[type] ?? []).filter((l) => l !== listener);
  }

  close() {
    this.closed = true;
  }

  emit(data: string) {
    for (const listener of this.listeners["message"] ?? []) {
      listener({ data } as MessageEvent<string>);
    }
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient();
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useSSE", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens a credentialed EventSource against the stream URL and handles a message", () => {
    const { unmount } = renderHook(() => useSSE(), { wrapper });

    expect(FakeEventSource.instances).toHaveLength(1);
    const source = FakeEventSource.instances[0];
    expect(source.url).toContain("/api/v1/events/stream");
    expect(source.withCredentials).toBe(true);

    // A message arriving before unmount should not throw — full
    // invalidation behaviour is covered by the handleSSEMessage tests above.
    expect(() =>
      source.emit(JSON.stringify({ type: "ticket_changed", ticket_id: 1, event: "x" })),
    ).not.toThrow();

    unmount();
    expect(source.closed).toBe(true);
  });

  it("does not open a connection when disabled", () => {
    renderHook(() => useSSE(false), { wrapper });
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});
