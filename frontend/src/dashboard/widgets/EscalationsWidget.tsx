import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import type { TicketListItem } from "@/lib/api";
import { humanDuration } from "@/lib/status";
import { cn } from "@/lib/cn";
import { ticketCustomer } from "@/components/agent/dashboard/ticketCustomer";
import { formatDurationSince } from "../legacy";
import { WidgetFrame, type FrameLink } from "../chrome/WidgetFrame";
import { useDrill } from "../drill/useDrill";
import { useWidgetData } from "../data";
import { WidgetDataBody, WidgetEmpty } from "../chrome/WidgetDataBody";
import { TicketLink } from "../chrome/TicketLink";

export interface EscalationsSettings {
  limit: number;
  /** 0 = only breached deadlines (today's strip); otherwise also those due within N hours. */
  within_hours: number;
}

export type EscalationKind = "first_response" | "update" | "solution" | "escalation";

export interface EscalationsData {
  items: { ticket: TicketListItem; epoch: number; kind: EscalationKind }[];
  total: number;
  oldest_epoch: number | null;
}

const KINDS: readonly string[] = ["first_response", "update", "solution", "escalation"];

/** Breached and upcoming deadlines, nearest first: kind · customer and the signed time. */
export function EscalationsWidget({ instanceId, settings, size, mode }: WidgetProps<EscalationsSettings>) {
  const { t } = useTranslation();
  const drill = useDrill(instanceId, mode);
  const { status, data } = useWidgetData<EscalationsData>();
  const total = status === "ok" && typeof data?.total === "number" ? data.total : 0;
  const link: FrameLink | undefined = drill && total > 0 ? { label: t("dashboards.state.showAll", { count: total }), ...drill() } : undefined;
  return (
    <WidgetFrame
      title={t("dashboards.widgets.escalations.title")}
      link={link}
      mode={mode}
      testId="escalations-widget"
      actions={<span className="shrink-0 text-xs text-muted">{t("dashboards.widgets.escalations.order")}</span>}
    >
      <WidgetDataBody<EscalationsData> h={size.h}>
        {(data) => {
          const items = Array.isArray(data?.items) ? data.items : [];
          if (items.length === 0)
            return (
              <WidgetEmpty>
                {settings.within_hours > 0
                  ? t("dashboards.widgets.escalations.emptyWithin", { count: settings.within_hours })
                  : t("dashboards.widgets.escalations.empty")}
              </WidgetEmpty>
            );
          const now = Date.now() / 1000;
          const navIds = items.map((x) => x.ticket.id);
          return (
            <ul className="flex flex-col gap-2">
              {items.map(({ ticket, epoch, kind }) => {
                const breached = epoch <= now;
                const customer = ticketCustomer(ticket).label;
                const kindLabel = t(`dashboards.widgets.escalations.kind.${KINDS.includes(kind) ? kind : "escalation"}`);
                return (
                  <li key={ticket.id}>
                    <TicketLink
                      ticketId={ticket.id}
                      navIds={navIds}
                      mode={mode}
                      testId={`escalation-${ticket.id}`}
                      className="flex items-center gap-3 rounded-[10px] bg-surface-subtle px-3 py-2.5 text-ink transition-colors hover:bg-accent-dim focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                    >
                      <span
                        aria-hidden="true"
                        className={cn("w-1 self-stretch rounded-sm", breached ? "bg-danger" : "bg-escalation")}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{ticket.title}</span>
                        <span className="block truncate text-xs text-muted">
                          {customer ? `${kindLabel} · ${customer}` : kindLabel}
                        </span>
                      </span>
                      <span
                        className={cn(
                          "shrink-0 font-mono text-xs font-semibold tabular-nums",
                          breached ? "text-danger" : "text-escalation",
                        )}
                      >
                        {breached
                          ? t("dashboards.due.since", { duration: formatDurationSince(epoch) })
                          : t("dashboards.widgets.escalations.remaining", { duration: humanDuration(t, epoch - now) })}
                      </span>
                    </TicketLink>
                  </li>
                );
              })}
            </ul>
          );
        }}
      </WidgetDataBody>
    </WidgetFrame>
  );
}
