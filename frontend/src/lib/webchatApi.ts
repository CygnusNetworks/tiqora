/**
 * Agent and admin wrappers for the web chat
 * (`backend/src/tiqora/api/v1/chat.py` and `…/admin/chat_configs.py`).
 *
 * Agent routes live under `/api/v1/chat` and `/api/v1/tickets/{id}/chat`
 * (never `/chat/…`, which belongs to the visitor widget). Visitor-supplied
 * strings in these payloads are plain text and must only ever be rendered as
 * text nodes.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import type { Schemas } from "@tiqora/api-client";
import { api, ApiError } from "./api";

export type ChatAgentState = Schemas["ChatAgentStateOut"];
export type ChatAgentStateIn = Schemas["ChatAgentStateIn"];
export type ChatTopicRef = Schemas["ChatTopicRefOut"];
export type ChatInbox = Schemas["ChatInboxOut"];
export type ChatInboxItem = Schemas["ChatInboxItemOut"];
export type TicketChat = Schemas["TicketChatOut"];
export type ChatConfig = Schemas["ChatConfigOut"];
export type ChatConfigValues = Schemas["ChatConfigValues"];
export type ChatConfigCreate = Schemas["ChatConfigCreateIn"];
export type ChatConfigUpdate = Schemas["ChatConfigUpdateIn"];
export type ChatAccept = Schemas["ChatAcceptOut"];

export const chatAgentStateKey = ["chat", "agent-state"] as const;
export const chatInboxKey = ["chat", "inbox"] as const;
export const chatTopicsKey = ["chat", "topics"] as const;
export const chatConfigsKey = ["admin", "chat-configs"] as const;
export function ticketChatKey(ticketId: number) {
  return ["tickets", ticketId, "chat"] as const;
}

const sid = (sessionId: string) => encodeURIComponent(sessionId);

export const webchatApi = {
  getAgentState(signal?: AbortSignal) {
    return api.request<ChatAgentState>("GET", "/api/v1/chat/agent-state", { signal });
  },
  putAgentState(body: ChatAgentStateIn) {
    return api.request<ChatAgentState>("PUT", "/api/v1/chat/agent-state", { body });
  },
  topics(signal?: AbortSignal) {
    return api.request<ChatTopicRef[]>("GET", "/api/v1/chat/topics", { signal });
  },
  inbox(signal?: AbortSignal) {
    return api.request<ChatInbox>("GET", "/api/v1/chat/inbox", { signal });
  },
  accept(sessionId: string) {
    return api.request<ChatAccept>("POST", `/api/v1/chat/sessions/${sid(sessionId)}/accept`);
  },
  end(sessionId: string, closeTicket: boolean) {
    return api.request<void>("POST", `/api/v1/chat/sessions/${sid(sessionId)}/end`, {
      body: { close_ticket: closeTicket },
    });
  },
  ticketChat(ticketId: number, signal?: AbortSignal) {
    return api.request<TicketChat>("GET", `/api/v1/tickets/${ticketId}/chat`, { signal });
  },
  typing(ticketId: number) {
    return api.request<void>("POST", `/api/v1/tickets/${ticketId}/chat/typing`);
  },
  listConfigs(signal?: AbortSignal) {
    return api.request<ChatConfig[]>("GET", "/api/v1/admin/chat-configs", { signal });
  },
  getConfig(id: number, signal?: AbortSignal) {
    return api.request<ChatConfig>("GET", `/api/v1/admin/chat-configs/${id}`, { signal });
  },
  createConfig(body: ChatConfigCreate) {
    return api.request<ChatConfig>("POST", "/api/v1/admin/chat-configs", { body });
  },
  updateConfig(id: number, body: ChatConfigUpdate) {
    return api.request<ChatConfig>("PATCH", `/api/v1/admin/chat-configs/${id}`, { body });
  },
  deleteConfig(id: number) {
    return api.request<void>("DELETE", `/api/v1/admin/chat-configs/${id}`);
  },
  rotateKey(id: number) {
    return api.request<ChatConfig>("POST", `/api/v1/admin/chat-configs/${id}/rotate-key`);
  },
};

/** The ticket's chat header data; `null` when the ticket has no chat (404). */
export function useTicketChat(ticketId: number, enabled: boolean): UseQueryResult<TicketChat | null> {
  return useQuery<TicketChat | null>({
    queryKey: ticketChatKey(ticketId),
    enabled,
    retry: false,
    queryFn: async ({ signal }) => {
      try {
        return await webchatApi.ticketChat(ticketId, signal);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
  });
}

/** A chat the agent can still type into (a reply to a waiting one accepts it first). */
export function isChatLive(chat: TicketChat | null | undefined): boolean {
  return chat?.status === "waiting" || chat?.status === "active";
}

/** Addresses in the reserved `.invalid` TLD (the placeholder a visitor
 * without an e-mail address gets) must never be mailed. */
export function isInvalidTldAddress(email: string): boolean {
  return /\.invalid$/i.test(email.trim().replace(/^.*<|>$/g, "").trim());
}
