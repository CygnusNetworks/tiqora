import { useQuery } from "@tanstack/react-query";
import { ApiError, api } from "./api";

/** Under `["tickets", id]` so the SSE `ticket_changed` invalidation of
 * `["tickets"]` refreshes it along with the articles. */
export function telegramChatKey(ticketId: number) {
  return ["tickets", ticketId, "telegram"] as const;
}

/**
 * Chat meta of a Telegram ticket (contact, consent, per-message map rows).
 * Resolves to `null` when the ticket has no Telegram chat (404) — callers
 * then render neither header nor bubble extras.
 */
export function useTelegramChat(ticketId: number, enabled: boolean) {
  return useQuery({
    queryKey: telegramChatKey(ticketId),
    queryFn: async ({ signal }) => {
      try {
        return await api.getTelegramChat(ticketId, signal);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    enabled,
  });
}
