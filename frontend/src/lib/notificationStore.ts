import { useSyncExternalStore } from "react";

/**
 * Tiny module-level notification store (no extra dependency — plain
 * `useSyncExternalStore`). Holds the live, session-only list of
 * "new mail in your queue" notifications driven by the SSE
 * `ticket_new_in_queue` event. There is deliberately no persistence: the
 * backend keeps no notification table (see api/v1/events.py), so unread
 * state resets on reload — matching the v1 limitation documented there.
 */

export type NotificationItem = {
  /** Stable per-arrival id (not the ticket id — a ticket can notify twice). */
  id: string;
  ticketId: number;
  tn: string;
  title: string;
  queueName: string;
  receivedAt: number;
  read: boolean;
};

type State = {
  items: NotificationItem[];
  unreadCount: number;
};

/** Cap retained items so a long-lived session can't grow unbounded. */
const MAX_ITEMS = 50;

let state: State = { items: [], unreadCount: 0 };
const listeners = new Set<() => void>();

function setState(next: State): void {
  state = next;
  for (const listener of listeners) listener();
}

function unreadOf(items: NotificationItem[]): number {
  return items.reduce((n, i) => (i.read ? n : n + 1), 0);
}

export function addNotification(input: {
  ticketId: number;
  tn: string;
  title: string;
  queueName: string;
}): NotificationItem {
  const item: NotificationItem = {
    id: `${input.ticketId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    ticketId: input.ticketId,
    tn: input.tn,
    title: input.title,
    queueName: input.queueName,
    receivedAt: Date.now(),
    read: false,
  };
  const items = [item, ...state.items].slice(0, MAX_ITEMS);
  setState({ items, unreadCount: unreadOf(items) });
  return item;
}

export function markAllNotificationsRead(): void {
  if (state.unreadCount === 0) return;
  setState({ items: state.items.map((i) => ({ ...i, read: true })), unreadCount: 0 });
}

export function markNotificationRead(id: string): void {
  const items = state.items.map((i) => (i.id === id ? { ...i, read: true } : i));
  setState({ items, unreadCount: unreadOf(items) });
}

export function clearNotifications(): void {
  setState({ items: [], unreadCount: 0 });
}

function subscribe(callback: () => void): () => void {
  listeners.add(callback);
  return () => {
    listeners.delete(callback);
  };
}

function getSnapshot(): State {
  return state;
}

/** React hook: subscribe a component to the notification store. */
export function useNotifications(): State {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
