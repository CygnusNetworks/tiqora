import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { humanDuration } from "@/lib/status";
import { TicketLink } from "../chrome/TicketLink";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetDataBody, WidgetEmpty } from "../chrome/WidgetDataBody";

export interface ChatQueueSettings {
  limit: number;
}

export interface ChatQueueItem {
  session_id: number;
  ticket_id: number;
  tn: string;
  title: string | null;
  topic_label: string | null;
  waiting_since: string | null;
}

export interface ChatQueueData {
  enabled: boolean;
  /** Counted up to the inbox cap (200). */
  waiting: number;
  oldest_waiting_since: string | null;
  mine: number;
  /** Absent on a wallboard without details. */
  items?: ChatQueueItem[];
}

/** The inbox cap of the provider; at the cap the count reads "200+". */
export const CHAT_WAITING_CAP = 200;

function secondsSince(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : Math.max(0, (Date.now() - ms) / 1000);
}

/** Web chats waiting for an agent, how long the oldest one has waited, and how many are mine. */
export function ChatQueueWidget({ settings, size, mode }: WidgetProps<ChatQueueSettings>) {
  const { t } = useTranslation();
  return (
    <WidgetFrame title={t("dashboards.widgets.chatQueue.title")} mode={mode} testId="chat-queue-widget">
      <WidgetDataBody<ChatQueueData> h={size.h}>
        {(d) => {
          if (!d) return <WidgetEmpty>{t("dashboards.widgets.chatQueue.disabled")}</WidgetEmpty>;
          if (!d.enabled) return <WidgetEmpty>{t("dashboards.widgets.chatQueue.disabled")}</WidgetEmpty>;
          const oldest = secondsSince(d.oldest_waiting_since);
          const items = Array.isArray(d.items) ? d.items.slice(0, settings.limit) : [];
          const waitingText = d.waiting >= CHAT_WAITING_CAP ? `${CHAT_WAITING_CAP}+` : String(d.waiting);
          return (
            <div className="flex h-full min-h-0 flex-col gap-3">
              <div className="flex items-end gap-6">
                <div>
                  <div className="font-mono text-3xl font-semibold tabular-nums text-ink" data-testid="chat-waiting">
                    {waitingText}
                  </div>
                  <div className="text-xs text-muted">{t("dashboards.widgets.chatQueue.waiting")}</div>
                </div>
                <div>
                  <div className="font-mono text-xl font-semibold tabular-nums text-ink" data-testid="chat-mine">
                    {d.mine}
                  </div>
                  <div className="text-xs text-muted">{t("dashboards.widgets.chatQueue.mine")}</div>
                </div>
              </div>
              {d.waiting > 0 && oldest !== null && (
                <p className="text-xs text-muted" data-testid="chat-oldest">
                  {t("dashboards.widgets.chatQueue.oldest", { d: humanDuration(t, oldest) })}
                </p>
              )}
              {d.waiting === 0 && <p className="text-sm text-muted">{t("dashboards.widgets.chatQueue.none")}</p>}
              {items.length > 0 && (
                <ul className="flex min-h-0 flex-col gap-1.5 overflow-hidden">
                  {items.map((item) => {
                    const since = secondsSince(item.waiting_since);
                    return (
                      <li key={item.session_id}>
                        <TicketLink
                          ticketId={item.ticket_id}
                          navIds={items.map((i) => i.ticket_id)}
                          mode={mode}
                          testId={`chat-item-${item.session_id}`}
                          className="flex items-center gap-3 rounded-[10px] bg-surface-subtle px-3 py-2 text-ink transition-colors hover:bg-accent-dim focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                        >
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium">{item.title || item.tn}</span>
                            {item.topic_label && <span className="block truncate text-xs text-muted">{item.topic_label}</span>}
                          </span>
                          {since !== null && (
                            <span className="shrink-0 font-mono text-xs tabular-nums text-muted">
                              {t("dashboards.widgets.chatQueue.since", { d: humanDuration(t, since) })}
                            </span>
                          )}
                        </TicketLink>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          );
        }}
      </WidgetDataBody>
    </WidgetFrame>
  );
}
