import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { api, ApiError } from "./api";
import { chatInboxKey, isChatLive, ticketChatKey, useTicketChat, webchatApi } from "./webchatApi";
import { resolveWebchatData } from "@/demo/webchatMock";
import { resolveData } from "@/demo/mockData";

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("webchatApi", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("uses the agent routes, never /chat/", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await webchatApi.inbox();
    await webchatApi.accept("s 1");
    await webchatApi.end("s1", true);
    await webchatApi.typing(5);
    const calls = spy.mock.calls.map((c) => `${c[0]} ${c[1]}`);
    expect(calls).toEqual([
      "GET /api/v1/chat/inbox",
      "POST /api/v1/chat/sessions/s%201/accept",
      "POST /api/v1/chat/sessions/s1/end",
      "POST /api/v1/tickets/5/chat/typing",
    ]);
    expect(spy.mock.calls[2][2]).toMatchObject({ body: { close_ticket: true } });
  });

  it("keys", () => {
    expect(chatInboxKey).toEqual(["chat", "inbox"]);
    expect(ticketChatKey(7)).toEqual(["tickets", 7, "chat"]);
  });

  it("useTicketChat returns null on 404", async () => {
    vi.spyOn(api, "request").mockRejectedValue(new ApiError(404, "not found", "/x"));
    const { result } = renderHook(() => useTicketChat(9, true), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it("useTicketChat polls while the visitor types, slowly while live, never when ended (F-M1)", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const w = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const chat = { status: "active", visitor_typing: true, visitor_online: true };
    vi.spyOn(api, "request").mockResolvedValue(chat as never);
    const { result } = renderHook(() => useTicketChat(9, true), { wrapper: w });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const query = qc.getQueryCache().find({ queryKey: ticketChatKey(9) })!;
    const interval = (query.options as { refetchInterval?: unknown }).refetchInterval as (q: typeof query) => number | false;
    expect(interval(query)).toBe(6000);
    query.setData({ ...chat, visitor_typing: false });
    expect(interval(query)).toBe(30_000);
    query.setData({ ...chat, visitor_typing: false, status: "ended" });
    expect(interval(query)).toBe(false);
    query.setData(null);
    expect(interval(query)).toBe(false);
  });

  it("useTicketChat surfaces other errors", async () => {
    vi.spyOn(api, "request").mockRejectedValue(new ApiError(500, "boom", "/x"));
    const { result } = renderHook(() => useTicketChat(9, true), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe("webchat demo data", () => {
  it("is path-scoped: other paths fall through", () => {
    expect(resolveWebchatData("/api/v1/tickets/100", "GET")).toBeUndefined();
    expect(resolveWebchatData("/api/v1/reports/run", "POST")).toBeUndefined();
    expect(resolveWebchatData("/api/v1/chat/inbox", "GET")).toMatchObject({ waiting: [{ ticket_id: 119 }, { ticket_id: 117 }], bot: [{ ticket_id: 118 }], mine: [{ ticket_id: 116 }] });
  });

  it("serves ticket 117 with a visitor article", () => {
    expect(resolveData("/api/v1/tickets/117/articles", "GET") as unknown[]).toHaveLength(1);
  });

  it("serves the bot chat (118) and the handed-over chat (119) as WebChat tickets", () => {
    for (const id of [118, 119]) {
      const arts = resolveData(`/api/v1/tickets/${id}/articles`, "GET") as { communication_channel_name: string }[];
      expect(arts.length).toBeGreaterThan(0);
      expect(arts.every((a) => a.communication_channel_name === "WebChat")).toBe(true);
      expect(resolveData(`/api/v1/tickets/${id}`, "GET")).toMatchObject({ id });
    }
    expect(resolveData("/api/v1/tickets/118/chat", "GET")).toMatchObject({ status: "bot", bot_name: "Mia" });
    expect(resolveData("/api/v1/tickets/119/chat", "GET")).toMatchObject({ status: "waiting", handoff_reason: "ai" });
  });

  it("demo accept returns the ticket of the accepted session", () => {
    const accept = (sid: string) => resolveData(`/api/v1/chat/sessions/${sid}/accept`, "POST");
    expect(accept("c4a1f0d2-7e63-4b85-9a2c-1d3e5f708b93")).toEqual({ ticket_id: 118 });
    expect(accept("e2b7a9c1-4d58-4a36-8f0e-9c1b2a3d4e64")).toEqual({ ticket_id: 119 });
    expect(accept("9f3c2a40-5b1e-4d7a-8c2e-0a1b2c3d4e51")).toEqual({ ticket_id: 117 });
  });

  it("serves ticket 116 as a webchat ticket with three WebChat articles", () => {
    const arts = resolveData("/api/v1/tickets/116/articles", "GET") as { communication_channel_name: string }[];
    expect(arts).toHaveLength(3);
    expect(arts.every((a) => a.communication_channel_name === "WebChat")).toBe(true);
    expect(resolveData("/api/v1/tickets/116/chat", "GET")).toMatchObject({ visitor_online: true, status: "active" });
    expect(resolveData("/api/v1/tickets/100/chat", "GET")).toBeNull();
  });
});

describe("isChatLive", () => {
  it.each([
    ["bot", true],
    ["waiting", true],
    ["active", true],
    ["ended", false],
  ])("status %s -> %s", (status, live) => {
    expect(isChatLive({ status } as Parameters<typeof isChatLive>[0])).toBe(live);
  });
  it("is false without a chat", () => {
    expect(isChatLive(null)).toBe(false);
  });
});
