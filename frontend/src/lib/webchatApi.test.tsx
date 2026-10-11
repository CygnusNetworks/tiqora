import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { api, ApiError } from "./api";
import { chatInboxKey, ticketChatKey, useTicketChat, webchatApi } from "./webchatApi";
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
    expect(resolveWebchatData("/api/v1/chat/inbox", "GET")).toMatchObject({ waiting: [{}], mine: [{ ticket_id: 116 }] });
  });

  it("serves ticket 117 with a visitor article", () => {
    expect(resolveData("/api/v1/tickets/117/articles", "GET") as unknown[]).toHaveLength(1);
  });

  it("serves ticket 116 as a webchat ticket with three WebChat articles", () => {
    const arts = resolveData("/api/v1/tickets/116/articles", "GET") as { communication_channel_name: string }[];
    expect(arts).toHaveLength(3);
    expect(arts.every((a) => a.communication_channel_name === "WebChat")).toBe(true);
    expect(resolveData("/api/v1/tickets/116/chat", "GET")).toMatchObject({ visitor_online: true, status: "active" });
    expect(resolveData("/api/v1/tickets/100/chat", "GET")).toBeNull();
  });
});
