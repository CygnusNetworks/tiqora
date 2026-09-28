import { useEffect, useRef } from "react";

/**
 * Hand-off from anywhere in the Telegram ticket view (bubble "Zitieren",
 * header shortcuts, AiPanel "Entwurf übernehmen") to the chat composer,
 * which lives in a different subtree. Module-level on purpose: the senders
 * and the composer share nothing but the ticket id, and threading a context
 * or callback through ArticleMasterDetail for a fire-and-forget signal isn't
 * worth it.
 */
export type ComposerRequest = {
  quoteArticleId?: number;
  focus?: boolean;
  /** An AI draft (inline suggestion or AiPanel's) to load into the body —
   * applied the same way as the composer's own "Übernehmen": an overwrite
   * confirm when there is already different, non-empty text. */
  draft?: { id: number; body: string };
};

type Handler = (req: ComposerRequest) => void;

const subscribers = new Map<number, Set<Handler>>();
// A request that arrives with nobody subscribed yet — e.g. the header just
// asked for the conversation view (below), which mounts the composer only on
// its *next* render — is held here and replayed once to the first
// subscriber that shows up, so the switch-then-focus hand-off never loses
// the "focus" (or quote/draft) half of the request.
const pending = new Map<number, ComposerRequest>();

export function requestComposer(ticketId: number, req: ComposerRequest): void {
  const set = subscribers.get(ticketId);
  if (set && set.size > 0) {
    set.forEach((h) => h(req));
  } else {
    pending.set(ticketId, req);
  }
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
    const queued = pending.get(ticketId);
    if (queued) {
      pending.delete(ticketId);
      h(queued);
    }
    return () => {
      const current = subscribers.get(ticketId);
      current?.delete(h);
      if (current?.size === 0) subscribers.delete(ticketId);
    };
  }, [ticketId]);
}

/**
 * Second, independent hand-off: switches a Telegram ticket the agent had
 * manually set to "split" back to "conversation" so the composer above is
 * actually there to receive a `requestComposer` call. Kept separate from
 * `ComposerRequest` because the receiver (`useArticleView`, owned by
 * `ArticleMasterDetail`) is a different subtree than the composer itself —
 * a view mode isn't a composer concern.
 */
type ViewHandler = () => void;

const viewSubscribers = new Map<number, Set<ViewHandler>>();

export function requestConversationView(ticketId: number): void {
  viewSubscribers.get(ticketId)?.forEach((h) => h());
}

export function useConversationViewRequests(ticketId: number, handler: ViewHandler): void {
  const latest = useRef(handler);
  latest.current = handler;

  useEffect(() => {
    const h: ViewHandler = () => latest.current();
    let set = viewSubscribers.get(ticketId);
    if (!set) {
      set = new Set();
      viewSubscribers.set(ticketId, set);
    }
    set.add(h);
    return () => {
      const current = viewSubscribers.get(ticketId);
      current?.delete(h);
      if (current?.size === 0) viewSubscribers.delete(ticketId);
    };
  }, [ticketId]);
}
