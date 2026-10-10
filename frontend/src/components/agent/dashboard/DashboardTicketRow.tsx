import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import type { TicketListItem } from "@/lib/api";
import { Avatar } from "@/components/ui/Avatar";
import { PriorityChip, StateChip } from "@/components/ui/StatusChip";
import { initialsOf } from "@/components/customers/customerFormat";
import { cn } from "@/lib/cn";
import { useShowAvatars } from "@/lib/showAvatars";
import { setTicketNavContext } from "@/lib/ticketNavContext";
import type { ColumnKey } from "@/dashboard/definitions";
import { ticketCustomer } from "./ticketCustomer";

/** Whether `key` is shown: no `columns` = today's look (every piece). */
const shows = (columns: ColumnKey[] | undefined, key: ColumnKey) => !columns || columns.includes(key);

/**
 * A single ticket row for the dashboard work lists: number + title, then a
 * compact meta cluster (queue, state soft-chip, priority soft-chip). The
 * right-hand `trailing` slot carries whatever each list needs there — a
 * changed date, an escalation countdown, or a due time. `escalated` adds the
 * red left border used to flag overdue rows in the escalated group/view.
 * With avatars on (AccountMenu), the customer's picture leads the row.
 *
 * Dashboard widgets pass `density` (compact = tighter rows), `columns` (which of number, title,
 * customer, queue, state and priority show; none given = today's look, where the customer is the
 * avatar only) and `linkMode` (`inert` = not a tab stop while editing, `none` = plain text in
 * previews). A chosen `customer` column shows the customer under the title (Mobile mockup).
 */
export function DashboardTicketRow({
  ticket,
  trailing,
  escalated,
  density = "comfortable",
  columns,
  linkMode = "link",
}: {
  ticket: TicketListItem;
  trailing?: ReactNode;
  escalated?: boolean;
  density?: "compact" | "comfortable";
  columns?: ColumnKey[];
  linkMode?: "link" | "inert" | "none";
}) {
  const { t } = useTranslation();
  const showAvatars = useShowAvatars();
  const { label: customerLabel, avatarEmail } = ticketCustomer(ticket);
  const rowClass = cn(
    "flex items-center gap-3 px-4 text-sm transition-colors duration-100",
    density === "compact" ? "py-1.5" : "py-2.5",
    linkMode !== "none" &&
      "hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
  );
  const content = (
    <>
      {showAvatars && customerLabel && (
        <span className="flex shrink-0" title={customerLabel}>
          <Avatar
            email={avatarEmail}
            domainFallback={false}
            initials={initialsOf(customerLabel)}
            size={20}
            testId={`dashboard-ticket-${ticket.id}-avatar`}
          />
        </span>
      )}
      {shows(columns, "tn") && (
        <span className="shrink-0 font-mono text-xs text-accent">{ticket.tn}</span>
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate">
          {shows(columns, "title") || !shows(columns, "tn") ? ticket.title : null}
        </span>
        {columns?.includes("customer") && customerLabel && (
          <span
            className="block truncate text-xs text-muted"
            data-testid={`dashboard-ticket-${ticket.id}-customer`}
          >
            {customerLabel}
          </span>
        )}
      </span>

      <span className="hidden shrink-0 items-center gap-2.5 sm:flex">
        {shows(columns, "queue") && ticket.queue_name && (
          <span
            className="max-w-[9rem] truncate text-xs text-muted"
            title={ticket.queue_name}
          >
            {ticket.queue_name.includes("::")
              ? ticket.queue_name.split("::").pop()
              : ticket.queue_name}
          </span>
        )}
        {shows(columns, "state") && (
          <StateChip
            state={ticket.state}
            data-testid={`dashboard-ticket-${ticket.id}-state-chip`}
          />
        )}
        {shows(columns, "priority") && (
          <PriorityChip
            priority={ticket.priority}
            priorityId={ticket.priority_id}
            data-testid={`dashboard-ticket-${ticket.id}-priority`}
          />
        )}
      </span>

      {trailing && (
        <span className="shrink-0 font-mono text-xs tabular-nums text-muted">{trailing}</span>
      )}
    </>
  );
  return (
    <li className={cn(escalated && "border-l-2 border-danger")}>
      {linkMode === "none" ? (
        <div data-testid={`dashboard-ticket-${ticket.id}`} data-density={density} className={rowClass}>
          {content}
        </div>
      ) : (
        <Link
          to="/agent/tickets/$ticketId"
          params={{ ticketId: String(ticket.id) }}
          tabIndex={linkMode === "inert" ? -1 : undefined}
          onClick={() =>
            setTicketNavContext({ label: t("nav.dashboard"), to: "/agent", ids: [ticket.id] })
          }
          data-testid={`dashboard-ticket-${ticket.id}`}
          data-density={density}
          className={rowClass}
        >
          {content}
        </Link>
      )}
    </li>
  );
}
