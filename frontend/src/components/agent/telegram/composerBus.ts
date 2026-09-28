import { useEffect, useRef } from "react";

/**
 * Hand-off from anywhere in the Telegram ticket view (bubble "Zitieren",
 * header shortcuts) to the chat composer, which lives in a different
 * subtree. Module-level on purpose: the senders and the composer share
 * nothing but the ticket id, and threading a context or callback through
 * ArticleMasterDetail for a fire-and-forget signal isn't worth it.
 */
export type ComposerRequest = { quoteArticleId?: number; focus?: boolean };

type Handler = (req: ComposerRequest) => void;

const subscribers = new Map<number, Set<Handler>>();

export function requestComposer(ticketId: number, req: ComposerRequest): void {
  subscribers.get(ticketId)?.forEach((h) => h(req));
}

export function useComposerRequests(ticketId: number, handler: Handler): void {
  // Keep the latest handler without resubscribing on every render (callers
  // typically pass an inline closure).
  const latest = useRef(handler);
  latest.current = handler;

  useEffect(() => {
    const h: Handler = (req) => latest.current(req);
    let set = subscribers.get(ticketId);
    if (!set) {
      set = new Set();
      subscribers.set(ticketId, set);
    }
    set.add(h);
    return () => {
      const current = subscribers.get(ticketId);
      current?.delete(h);
      if (current?.size === 0) subscribers.delete(ticketId);
    };
  }, [ticketId]);
}
