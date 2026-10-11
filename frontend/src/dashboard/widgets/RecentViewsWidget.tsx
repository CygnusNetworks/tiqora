import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { toBcp47 } from "@/i18n";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";
import { getLastViews } from "@/lib/lastViews";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetEmpty } from "../chrome/WidgetDataBody";
import { TicketLink } from "../chrome/TicketLink";

export interface RecentViewsSettings {
  limit: number;
}

/**
 * Tickets this browser opened last (`getLastViews`, localStorage). Two rows tall it keeps today's
 * chip strip; taller it lists number, title and when (mockup).
 */
export function RecentViewsWidget({ settings, size, mode }: WidgetProps<RecentViewsSettings>) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const [entries] = useState(getLastViews);
  const shown = entries.slice(0, Math.max(1, settings.limit));
  const chips = size.h <= 2;
  return (
    <WidgetFrame title={t("dashboards.widgets.recentViews.title")} mode={mode} testId="recent-views-widget">
      {shown.length === 0 ? (
        <WidgetEmpty>{t("dashboards.widgets.recentViews.empty")}</WidgetEmpty>
      ) : (
        <ul className={cn(chips ? "flex flex-wrap gap-2" : "flex flex-col gap-0.5")}>
          {shown.map((e) => {
            const title = e.title || t("dashboards.widgets.recentViews.untitled");
            return (
              <li key={e.id} className="min-w-0">
                <TicketLink
                  ticketId={e.id}
                  navIds={shown.map((x) => x.id)}
                  mode={mode}
                  testId={`recent-view-${e.id}`}
                  className={
                    chips
                      ? "inline-flex max-w-xs items-center gap-1.5 rounded-full border border-hairline bg-surface px-3 py-1.5 text-[12.5px] text-ink transition-colors hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                      : "flex min-w-0 items-baseline gap-2.5 rounded py-1 text-sm text-ink hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                  }
                >
                  <span className="shrink-0 font-mono text-[11px] text-muted">{e.tn || `#${e.id}`}</span>
                  <span className={cn("truncate", !chips && "min-w-0 flex-1")}>{title}</span>
                  {!chips && <span className="shrink-0 text-xs text-muted">{formatRelative(new Date(e.at), locale)}</span>}
                </TicketLink>
              </li>
            );
          })}
        </ul>
      )}
    </WidgetFrame>
  );
}
