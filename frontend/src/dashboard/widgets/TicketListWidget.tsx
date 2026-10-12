import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { WidgetMode, WidgetProps } from "@/plugins/dashboardWidgets";
import type { TicketListItem } from "@/lib/api";
import { toBcp47 } from "@/i18n";
import { cn } from "@/lib/cn";
import { formatListTime } from "@/lib/format";
import { PriorityChip, StateChip } from "@/components/ui/StatusChip";
import { DashboardTicketRow } from "@/components/agent/dashboard/DashboardTicketRow";
import { ticketCustomer } from "@/components/agent/dashboard/ticketCustomer";
import { linkTabIndex, rowLinkMode } from "../chrome/mode";
import { useIsKiosk } from "../wallboard/kioskContext";
import { useWidgetData } from "../data";
import type { ColumnKey } from "../definitions";
import { WidgetFrame, useWidgetChrome, type FrameLink } from "../chrome/WidgetFrame";
import { useDrill } from "../drill/useDrill";
import { WidgetDataBody, WidgetEmpty } from "../chrome/WidgetDataBody";
import { TicketLink } from "../chrome/TicketLink";
import { DueText } from "./DueText";
import { ChooseSourceAction } from "./SourceAction";
import { widgetPreset, type TicketSource } from "./presets";

export interface TicketListSettings {
  source: TicketSource;
  columns: ColumnKey[];
  sort: "age" | "changed" | "priority" | "deadline";
  order: "asc" | "desc";
  limit: number;
  density: "compact" | "comfortable";
}

export interface TicketListData {
  items: TicketListItem[];
  total: number;
}

/** A rendered column: number and title together form the mockup's one "Ticket" column. */
type Col = ColumnKey | "ticket";

/** The configured columns in order; `tn` + `title` collapse into one "Ticket" column. */
function tableColumns(columns: ColumnKey[]): Col[] {
  const both = columns.includes("tn") && columns.includes("title");
  const out: Col[] = [];
  for (const c of columns) {
    if (both && (c === "tn" || c === "title")) {
      if (!out.includes("ticket")) out.push("ticket");
    } else if (!out.includes(c)) out.push(c);
  }
  return out;
}

const shortQueue = (name: string | null | undefined) => (name ? (name.split("::").pop() ?? name) : "—");

function Cell({
  col,
  ticket,
  mode,
  navIds,
  locale,
  yesterday,
}: {
  col: Col;
  ticket: TicketListItem;
  mode: WidgetMode;
  navIds: number[];
  locale: string;
  yesterday: string;
}): ReactNode {
  const link = (children: ReactNode, className: string) => (
    <TicketLink ticketId={ticket.id} navIds={navIds} mode={mode} className={className}>
      {children}
    </TicketLink>
  );
  switch (col) {
    case "ticket":
      return link(
        <>
          <span className="block font-mono text-[11px] text-muted">{ticket.tn}</span>
          <span className="block truncate font-medium text-ink">{ticket.title}</span>
        </>,
        "block min-w-0 rounded hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
      );
    case "tn":
      return link(ticket.tn, "font-mono text-xs text-accent hover:underline");
    case "title":
      return link(ticket.title, "block truncate font-medium text-ink hover:text-accent");
    case "customer":
      return <span className="block truncate text-muted">{ticketCustomer(ticket).label ?? "—"}</span>;
    case "state":
      return <StateChip state={ticket.state} />;
    case "due":
      return <DueText ticket={ticket} testId={`ticket-${ticket.id}-due`} />;
    case "queue":
      return (
        <span className="block truncate text-muted" title={ticket.queue_name ?? undefined}>
          {shortQueue(ticket.queue_name)}
        </span>
      );
    case "owner":
      return <span className="block truncate text-muted">{ticket.owner_name || "—"}</span>;
    case "priority":
      return <PriorityChip priority={ticket.priority} priorityId={ticket.priority_id} />;
    case "changed":
      return <span className="text-muted">{formatListTime(ticket.change_time, locale, yesterday)}</span>;
    case "created":
      return <span className="text-muted">{formatListTime(ticket.create_time, locale, yesterday)}</span>;
  }
}

const HEADER_KEY: Record<Col, string> = {
  ticket: "dashboards.columns.ticket",
  tn: "dashboards.columns.tn",
  title: "dashboards.columns.title",
  customer: "dashboards.columns.customer",
  state: "dashboards.columns.state",
  due: "dashboards.columns.due",
  queue: "dashboards.columns.queue",
  owner: "dashboards.columns.owner",
  priority: "dashboards.columns.priority",
  changed: "dashboards.columns.changed",
  created: "dashboards.columns.created",
};

/** Tickets of a preset as the mockup's table (stacked rows on phones). */
export function TicketListWidget({ instanceId, settings, size, mode }: WidgetProps<TicketListSettings>) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const chrome = useWidgetChrome();
  const kiosk = useIsKiosk();
  const drill = useDrill(instanceId, mode);
  const { status, data } = useWidgetData<TicketListData>();
  const preset = widgetPreset(settings, kiosk);
  const items = Array.isArray(data?.items) ? data.items : [];
  const total = typeof data?.total === "number" ? data.total : items.length;
  const more = status === "ok" && total > items.length;
  // The drill page lists exactly the tickets of this widget (same code path on the server), so
  // "Alle N anzeigen" is a link for every kind of source.
  const link: FrameLink | undefined = more && drill ? { label: t("dashboards.state.showAll", { count: total }), ...drill() } : undefined;
  const cols = tableColumns(settings.columns);
  const navIds = items.map((x) => x.id);
  const density = settings.density === "compact" ? "compact" : "comfortable";
  const pad = density === "compact" ? "py-1.5" : "py-2.5";
  const yesterday = t("queue.day.yesterday");
  return (
    <WidgetFrame
      title={preset ? t(`dashboards.presets.${preset}.list`) : t("dashboards.widgets.ticketList.title")}
      link={link}
      actions={
        more && !link ? (
          <span className="shrink-0 text-xs text-muted">{t("dashboards.state.total", { count: total })}</span>
        ) : undefined
      }
      mode={mode}
      testId="ticket-list-widget"
    >
      <WidgetDataBody<TicketListData> h={size.h} forbiddenAction={<ChooseSourceAction mode={mode} />}>
        {() =>
          items.length === 0 ? (
            <WidgetEmpty
              action={
                chrome.canEdit &&
                chrome.onConfigure && (
                  <button
                    type="button"
                    onClick={chrome.onConfigure}
                    tabIndex={linkTabIndex(mode)}
                    className="text-sm font-medium text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                  >
                    {t("dashboards.widgets.ticketList.changeFilter")}
                  </button>
                )
              }
            >
              {t("dashboards.widgets.ticketList.empty")}
            </WidgetEmpty>
          ) : (
            <>
              <div className="hidden overflow-x-auto sm:block">
                <table
                  data-testid="ticket-list-table"
                  className={cn("w-full table-fixed border-collapse text-sm", `density-${density}`)}
                >
                  <thead>
                    <tr className="text-start text-xs text-muted">
                      {cols.map((c) => (
                        <th
                          key={c}
                          scope="col"
                          className={cn(
                            "pb-2 pe-3 font-medium last:pe-0",
                            c === "due" ? "text-end" : "text-start",
                            c === "ticket" || c === "title" ? "w-1/2" : undefined,
                          )}
                        >
                          {t(HEADER_KEY[c])}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((ticket) => (
                      <tr key={ticket.id} data-testid={`ticket-list-row-${ticket.id}`} className="border-t border-hairline">
                        {cols.map((c) => (
                          <td
                            key={c}
                            className={cn(
                              "min-w-0 pe-3 align-middle last:pe-0",
                              pad,
                              c === "due" && "whitespace-nowrap text-end font-mono text-xs",
                            )}
                          >
                            <Cell col={c} ticket={ticket} mode={mode} navIds={navIds} locale={locale} yesterday={yesterday} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="-mx-4 divide-y divide-hairline sm:hidden">
                {items.map((ticket) => (
                  <DashboardTicketRow
                    key={ticket.id}
                    ticket={ticket}
                    density={density}
                    columns={settings.columns}
                    linkMode={rowLinkMode(mode, kiosk)}
                    trailing={settings.columns.includes("due") ? <DueText ticket={ticket} /> : undefined}
                  />
                ))}
              </ul>
            </>
          )
        }
      </WidgetDataBody>
    </WidgetFrame>
  );
}
