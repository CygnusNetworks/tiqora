import { useEffect } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

type TicketChangedMessage = {
  type: "ticket_changed";
  ticket_id: number;
  event: string;
};

type PresenceChangedMessage = {
  type: "presence_changed";
  ticket_id: number;
};

export type SSEMessage = TicketChangedMessage | PresenceChangedMessage;

/** Cache key used by TicketZoomPage's presence poll — kept here so useSSE's
 * invalidation and the query that reads it never drift apart. */
export function presenceQueryKey(ticketId: number) {
  return ["presence", ticketId] as const;
}

/** Parses one SSE `EventSource` message and applies the corresponding
 * TanStack Query cache invalidation. Exported standalone (not just used
 * inside the hook) so it's directly unit-testable without mounting a
 * component or mocking EventSource's constructor. */
export function handleSSEMessage(queryClient: QueryClient, raw: string): void {
  let message: SSEMessage;
  try {
    message = JSON.parse(raw) as SSEMessage;
  } catch {
    return;
  }

  if (message.type === "ticket_changed") {
    // Broad invalidation: matches queue ticket lists (["tickets", {...}]),
    // the ticket zoom query (["tickets", ticketId]), and nested queries
    // (["tickets", ticketId, "articles"], ".../history", etc) — they all
    // share the "tickets" key prefix, so one invalidation covers them.
    void queryClient.invalidateQueries({ queryKey: ["tickets"] });
    return;
  }

  if (message.type === "presence_changed") {
    // Design choice (see backend/src/tiqora/api/v1/events.py docstring):
    // presence state itself is never pushed over SSE, only this marker —
    // clients react by refetching GET .../presence instead.
    void queryClient.invalidateQueries({ queryKey: presenceQueryKey(message.ticket_id) });
  }
}

/**
 * Opens a single `EventSource` against `/api/v1/events/stream` for the
 * lifetime of the mounting component (intended to be mounted once, near
 * the app shell) and applies cache invalidation for every message.
 *
 * Session-cookie auth: `withCredentials: true` sends the same cookie the
 * rest of the agent app uses — no separate token plumbing needed.
 *
 * Defensive by design: if the endpoint is unreachable (e.g. unmocked in a
 * test/e2e environment), `EventSource` just fires `onerror` and retries on
 * its own schedule — no unhandled rejection, no crash, no visible effect
 * beyond the cache never getting SSE-driven invalidation.
 */
export function useSSE(enabled = true): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!enabled) return undefined;
    if (typeof window === "undefined" || typeof window.EventSource === "undefined") {
      return undefined;
    }

    const source = new EventSource(api.eventStreamUrl(), { withCredentials: true });

    const onMessage = (evt: MessageEvent<string>) => {
      handleSSEMessage(queryClient, evt.data);
    };
    source.addEventListener("message", onMessage);
    // No onerror handler needed: EventSource retries automatically, and we
    // don't want a transient network blip to surface as an app-level error.

    return () => {
      source.removeEventListener("message", onMessage);
      source.close();
    };
  }, [enabled, queryClient]);
}
