import { useCallback, useRef } from "react";
import { api } from "@/lib/api";

/** Telegram shows "typing…" for about 5 s per chat action, so one ping every
 * 4 s keeps the indicator on while the agent types without flooding the
 * gateway with a request per keystroke. */
export const TYPING_PING_INTERVAL_MS = 4000;

/**
 * Returns a callback for the composer's `onChange`. Driven by keystrokes
 * rather than by the text value, so restoring a draft or taking over an AI
 * suggestion doesn't tell the customer someone is typing — and once the
 * agent stops typing (or the composer unmounts) nothing pings any more.
 */
export function useTypingPing(ticketId: number): () => void {
  const lastRef = useRef<number | null>(null);
  return useCallback(() => {
    const now = Date.now();
    if (lastRef.current !== null && now - lastRef.current < TYPING_PING_INTERVAL_MS) return;
    lastRef.current = now;
    // Cosmetic for the customer; a failed ping must never disturb typing.
    api.postTelegramTyping(ticketId).catch(() => undefined);
  }, [ticketId]);
}
