import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { addNotification } from "@/lib/notificationStore";

type TicketChangedMessage = {
  type: "ticket_changed";
  ticket_id: number;
  event: string;
};

type PresenceChangedMessage = {
  type: "presence_changed";
  ticket_id: number;
};

type TicketNewInQueueMessage = {
  type: "ticket_new_in_queue";
  ticket_id: number;
  tn: string;
  title: string;
  queue_id: number;
  queue_name: string;
};

export type SSEMessage =
  | TicketChangedMessage
  | PresenceChangedMessage
  | TicketNewInQueueMessage;

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
    // A changed ticket can move between queues or open/closed state, which
    // shifts the sidebar's per-queue open-count badges — refresh those too
    // (own key, not under the "tickets" prefix above).
    void queryClient.invalidateQueries({ queryKey: ["queues"] });
    return;
  }

  if (message.type === "presence_changed") {
    // Design choice (see backend/src/tiqora/api/v1/events.py docstring):
    // presence state itself is never pushed over SSE, only this marker —
    // clients react by refetching GET .../presence instead.
    void queryClient.invalidateQueries({ queryKey: presenceQueryKey(message.ticket_id) });
    return;
  }

  if (message.type === "ticket_new_in_queue") {
    // Backend already filters these to the agent's readable queues, so any
    // that reach us are worth a bell + toast. Also refresh the ticket lists
    // and queue counts so the new item shows up without a manual reload.
    addNotification({
      ticketId: message.ticket_id,
      tn: message.tn,
      title: message.title,
      queueName: message.queue_name,
    });
    void queryClient.invalidateQueries({ queryKey: ["tickets"] });
    void queryClient.invalidateQueries({ queryKey: ["queues"] });
  }
}

/** Live state of the realtime event stream, surfaced in the top bar's
 * connection dot. "connecting" is the brief initial state; "live" once the
 * stream is open; "reconnecting" after a drop (EventSource retries on its
 * own). */
export type ConnectionState = "connecting" | "live" | "reconnecting";

const ConnectionContext = createContext<ConnectionState>("connecting");

/** Read the current SSE connection state. Defaults to "connecting" outside a
 * provider (e.g. the admin shell, which doesn't open the stream). */
export function useConnectionStatus(): ConnectionState {
  return useContext(ConnectionContext);
}

/**
 * Opens a single `EventSource` against `/api/v1/events/stream` for the
 * lifetime of the mounting component (intended to be mounted once, near
 * the app shell) and applies cache invalidation for every message. Also
 * tracks the stream's connection state and provides it to descendants via
 * `useConnectionStatus()` — wrap the app subtree in the returned element.
 *
 * Session-cookie auth: `withCredentials: true` sends the same cookie the
 * rest of the agent app uses — no separate token plumbing needed.
 *
 * Defensive by design: if the endpoint is unreachable (e.g. unmocked in a
 * test/e2e environment), `EventSource` just fires `onerror` and retries on
 * its own schedule — no unhandled rejection, no crash, only the dot turning
 * amber.
 */
export function SSEProvider({
  children,
  enabled = true,
}: {
  children: ReactNode;
  enabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<ConnectionState>("connecting");

  useEffect(() => {
    if (!enabled) return undefined;
    if (typeof window === "undefined" || typeof window.EventSource === "undefined") {
      return undefined;
    }

    const source = new EventSource(api.eventStreamUrl(), { withCredentials: true });

    const onMessage = (evt: MessageEvent<string>) => {
      handleSSEMessage(queryClient, evt.data);
    };
    const onOpen = () => setState("live");
    // EventSource retries automatically; onerror fires on each drop. Reflect
    // that as "reconnecting" rather than surfacing an app-level error.
    const onError = () => setState("reconnecting");

    source.addEventListener("message", onMessage);
    source.addEventListener("open", onOpen);
    source.addEventListener("error", onError);

    return () => {
      source.removeEventListener("message", onMessage);
      source.removeEventListener("open", onOpen);
      source.removeEventListener("error", onError);
      source.close();
    };
  }, [enabled, queryClient]);

  return createElement(ConnectionContext.Provider, { value: state }, children);
}
