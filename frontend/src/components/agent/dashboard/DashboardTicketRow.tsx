import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import type { TicketListItem } from "@/lib/api";
import { priorityName, priorityTextClass } from "@/lib/priority";
import { stateColorVar } from "@/lib/status";
import { cn } from "@/lib/cn";

/**
 * A single ticket row for the dashboard work lists: number + title, then a
 * compact meta cluster (queue, state chip with a colour dot, priority tinted
 * by severity). The right-hand `trailing` slot carries whatever each list
 * needs there — a changed date, an escalation countdown, or a due time.
 */
export function DashboardTicketRow({
  ticket,
  trailing,
}: {
  ticket: TicketListItem;
  trailing?: ReactNode;
}) {
  const prio = priorityName(ticket.priority);

  return (
    <li>
      <Link
        to="/agent/tickets/$ticketId"
        params={{ ticketId: String(ticket.id) }}
        data-testid={`dashboard-ticket-${ticket.id}`}
        className="flex items-center gap-3 px-4 py-2.5 text-sm transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
      >
        <span className="shrink-0 font-mono text-xs text-accent">{ticket.tn}</span>
        <span className="min-w-0 flex-1 truncate">{ticket.title}</span>

        <span className="hidden shrink-0 items-center gap-2.5 sm:flex">
          {ticket.queue_name && (
            <span
              className="max-w-[9rem] truncate text-xs text-muted"
              title={ticket.queue_name}
            >
              {ticket.queue_name.includes("::")
                ? ticket.queue_name.split("::").pop()
                : ticket.queue_name}
            </span>
          )}
          {ticket.state && (
            <span className="inline-flex items-center gap-1.5 text-xs text-muted">
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: stateColorVar(ticket.state) }}
                data-testid={`dashboard-ticket-${ticket.id}-state-dot`}
                aria-hidden
              />
              {ticket.state}
            </span>
          )}
          {prio && (
            <span
              className={cn("text-xs font-medium", priorityTextClass(ticket.priority_id))}
              data-testid={`dashboard-ticket-${ticket.id}-priority`}
            >
              {prio}
            </span>
          )}
        </span>

        {trailing && (
          <span className="shrink-0 font-mono text-xs tabular-nums text-muted">{trailing}</span>
        )}
      </Link>
    </li>
  );
}
