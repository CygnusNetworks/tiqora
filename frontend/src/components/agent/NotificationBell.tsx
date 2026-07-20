import { useEffect, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  markAllNotificationsRead,
  markNotificationRead,
  useNotifications,
  type NotificationItem,
} from "@/lib/notificationStore";
import { cn } from "@/lib/cn";
import { BellIcon } from "@/components/ui/icons";

/**
 * Topbar bell showing the live SSE `ticket_new_in_queue` notifications:
 * an unread-count pill and a dropdown list. Clicking an item opens the
 * ticket and marks it read. Session-only (no persistence — see the store).
 */
export function NotificationBell() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { items, unreadCount } = useNotifications();
  const [open, setOpen] = useState(false);

  const openItem = (item: NotificationItem) => {
    markNotificationRead(item.id);
    setOpen(false);
    void navigate({
      to: "/agent/tickets/$ticketId",
      params: { ticketId: String(item.ticketId) },
    });
  };

  return (
    <div className="relative">
      <button
        type="button"
        data-testid="notification-bell"
        aria-label={t("notifications.title")}
        onClick={() => {
          setOpen((o) => {
            const next = !o;
            if (next) markAllNotificationsRead();
            return next;
          });
        }}
        className="relative flex h-8 w-8 items-center justify-center rounded-lg text-ink/70 transition-colors duration-100 hover:bg-surface-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      >
        <BellIcon className="text-[17px]" />
        {unreadCount > 0 && (
          <span
            data-testid="notification-unread-count"
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[9px] font-bold tabular-nums text-accent-ink"
          >
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <>
          <button
            type="button"
            aria-hidden
            tabIndex={-1}
            className="fixed inset-0 z-30 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div
            data-testid="notification-panel"
            className="absolute right-0 z-40 mt-1.5 w-80 max-w-[90vw] overflow-hidden rounded-lg border border-hairline bg-surface shadow-xl"
          >
            <div className="border-b border-hairline px-3 py-2 text-[12px] font-semibold text-ink">
              {t("notifications.title")}
            </div>
            {items.length === 0 ? (
              <p className="px-3 py-6 text-center text-[12.5px] text-muted">
                {t("notifications.empty")}
              </p>
            ) : (
              <ul className="max-h-80 list-none overflow-y-auto">
                {items.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      data-testid={`notification-item-${item.ticketId}`}
                      onClick={() => openItem(item)}
                      className={cn(
                        "flex w-full flex-col gap-0.5 border-b border-hairline px-3 py-2 text-left transition-colors duration-100 last:border-b-0 hover:bg-surface-subtle",
                        !item.read && "bg-accent-dim/40",
                      )}
                    >
                      <span className="flex items-center gap-2">
                        <span className="font-mono text-[11px] tabular-nums text-accent">
                          {item.tn}
                        </span>
                        <span className="truncate text-[11px] text-muted">{item.queueName}</span>
                      </span>
                      <span className="truncate text-[12.5px] text-ink">
                        {item.title || t("notifications.noSubject")}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Transient toast overlay for freshly-arrived notifications. Mounted once
 * near the app shell; watches the store's newest item and shows a briefly
 * auto-dismissing card. Skips the initial snapshot so a reload doesn't
 * replay old items as toasts.
 */
export function NotificationToaster() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { items } = useNotifications();
  const [toasts, setToasts] = useState<NotificationItem[]>([]);
  const lastSeenId = useRef<string | null>(null);

  useEffect(() => {
    const top = items[0];
    if (!top) return;
    if (lastSeenId.current === null) {
      // First render — adopt current head without toasting existing items.
      lastSeenId.current = top.id;
      return;
    }
    if (top.id === lastSeenId.current) return;
    lastSeenId.current = top.id;
    setToasts((cur) => [top, ...cur].slice(0, 3));
    const timer = setTimeout(() => {
      setToasts((cur) => cur.filter((tt) => tt.id !== top.id));
    }, 5000);
    return () => clearTimeout(timer);
  }, [items]);

  if (toasts.length === 0) return null;

  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2"
      data-testid="notification-toaster"
    >
      {toasts.map((item) => (
        <button
          type="button"
          key={item.id}
          data-testid={`notification-toast-${item.ticketId}`}
          onClick={() => {
            markNotificationRead(item.id);
            setToasts((cur) => cur.filter((tt) => tt.id !== item.id));
            void navigate({
              to: "/agent/tickets/$ticketId",
              params: { ticketId: String(item.ticketId) },
            });
          }}
          className="pointer-events-auto w-72 max-w-[90vw] rounded-lg border border-hairline bg-surface p-3 text-left shadow-xl animate-route-in"
        >
          <div className="flex items-center gap-2">
            <BellIcon className="text-[14px] text-accent" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-accent">
              {t("notifications.newTicket")}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span className="font-mono text-[11px] tabular-nums text-accent">{item.tn}</span>
            <span className="truncate text-[11px] text-muted">{item.queueName}</span>
          </div>
          <p className="mt-0.5 truncate text-[12.5px] text-ink">
            {item.title || t("notifications.noSubject")}
          </p>
        </button>
      ))}
    </div>
  );
}
