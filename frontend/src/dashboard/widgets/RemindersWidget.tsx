import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import type { TicketListItem } from "@/lib/api";
import { toBcp47 } from "@/i18n";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { DashboardTicketRow } from "@/components/agent/dashboard/DashboardTicketRow";
import { rowLinkMode } from "../chrome/mode";
import { WidgetFrame, type FrameLink } from "../chrome/WidgetFrame";
import { useDrill } from "../drill/useDrill";
import { useWidgetData } from "../data";
import { WidgetDataBody, WidgetEmpty } from "../chrome/WidgetDataBody";

export interface RemindersSettings {
  window_hours: number;
  limit: number;
}

export interface RemindersData {
  items: { ticket: TicketListItem; until: number; overdue: boolean }[];
  total: number;
}

/** Waiting tickets whose reminder is overdue or due soon (today's rows; overdue in danger). */
export function RemindersWidget({ instanceId, size, mode }: WidgetProps<RemindersSettings>) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const drill = useDrill(instanceId, mode);
  const { status, data } = useWidgetData<RemindersData>();
  const total = status === "ok" && typeof data?.total === "number" ? data.total : 0;
  const link: FrameLink | undefined = drill && total > 0 ? { label: t("dashboards.state.showAll", { count: total }), ...drill() } : undefined;
  return (
    <WidgetFrame title={t("dashboards.widgets.reminders.title")} link={link} mode={mode} testId="reminders-widget">
      <WidgetDataBody<RemindersData> h={size.h}>
        {(data) => {
          const items = Array.isArray(data?.items) ? data.items : [];
          if (items.length === 0) return <WidgetEmpty>{t("dashboards.widgets.reminders.empty")}</WidgetEmpty>;
          return (
            <ul className="-mx-4 divide-y divide-hairline">
              {items.map(({ ticket, until, overdue }) => (
                <DashboardTicketRow
                  key={ticket.id}
                  ticket={ticket}
                  linkMode={rowLinkMode(mode)}
                  trailing={
                    <span data-testid={`reminder-${ticket.id}-due`} className={cn(overdue && "text-danger")}>
                      {overdue && (
                        <span className="me-1.5 font-sans font-semibold">
                          {t("dashboards.widgets.reminders.overdue")}
                        </span>
                      )}
                      {formatDateTime(new Date(until * 1000), locale)}
                    </span>
                  }
                />
              ))}
            </ul>
          );
        }}
      </WidgetDataBody>
    </WidgetFrame>
  );
}
