import { useCallback } from "react";
import { api } from "@/lib/api";
import {
  TYPING_PING_INTERVAL_MS,
  useTypingPing as useMessengerTypingPing,
} from "../messenger/useTypingPing";

export { TYPING_PING_INTERVAL_MS };

/** The shared typing throttle wired to the Telegram gateway. */
export function useTypingPing(ticketId: number): () => void {
  const send = useCallback(() => api.postTelegramTyping(ticketId), [ticketId]);
  return useMessengerTypingPing(send);
}
