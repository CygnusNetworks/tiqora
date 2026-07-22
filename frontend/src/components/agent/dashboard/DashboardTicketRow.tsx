import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import type { TicketListItem } from "@/lib/api";
import { PriorityChip, StateChip } from "@/components/ui/StatusChip";
import { cn } from "@/lib/cn";

/**
 * A single ticket row for the dashboard work lists: number + title, then a
 * compact meta cluster (queue, state soft-chip, priority soft-chip). The
 * right-hand `trailing` slot carries whatever each list needs there — a
 * changed date, an escalation countdown, or a due time. `escalated` adds the
 * red left border used to flag overdue rows in the escalated group/view.
 */
export function DashboardTicketRow({
  ticket,
  trailing,
  escalated,
}: {
  ticket: TicketListItem;
  trailing?: ReactNode;
  escalated?: boolean;
}) {
  return (
    <li className={cn(escalated && "border-l-2 border-danger")}>
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
          <StateChip
            state={ticket.state}
            data-testid={`dashboard-ticket-${ticket.id}-state-chip`}
          />
          <PriorityChip
            priority={ticket.priority}
            priorityId={ticket.priority_id}
            data-testid={`dashboard-ticket-${ticket.id}-priority`}
          />
        </span>

        {trailing && (
          <span className="shrink-0 font-mono text-xs tabular-nums text-muted">{trailing}</span>
        )}
      </Link>
    </li>
  );
}
