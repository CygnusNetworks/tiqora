import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { stateColorVar, stateLabel } from "@/lib/status";

type TicketLink = Awaited<ReturnType<typeof api.listTicketLinks>>[number];

function roleLabelKey(link: TicketLink): string {
  if (link.other_role === "parent") return "ticket.links.parent";
  if (link.other_role === "child") return "ticket.links.child";
  return "ticket.links.related";
}

/**
 * "Linked tickets" row of the ticket-zoom header: one chip per link with the
 * relation (parent / child / related), number, title and state of the other
 * ticket. Shares its query key with the link dialog, so adding a link there
 * shows up here without a reload. Renders nothing when there are no links.
 */
export function LinkedTickets({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const linksQ = useQuery({
    queryKey: ["tickets", ticketId, "links"],
    queryFn: () => api.listTicketLinks(ticketId),
  });
  const links = linksQ.data ?? [];
  if (links.length === 0) return null;

  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[12.5px]"
      data-testid="linked-tickets"
    >
      <span className="text-muted">{t("ticket.links.label")}</span>
      {links.map((l) => (
        <Link
          key={l.other_ticket_id}
          to="/agent/tickets/$ticketId"
          params={{ ticketId: String(l.other_ticket_id) }}
          className="inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-md border border-hairline bg-surface-subtle px-2 py-0.5 transition-colors duration-100 hover:border-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          title={`${l.other_tn ?? l.other_ticket_id} — ${l.other_title ?? ""}`}
          data-testid={`linked-ticket-${l.other_ticket_id}`}
        >
          <span
            aria-hidden
            className="h-2 w-2 flex-none rounded-full"
            style={{ background: stateColorVar(l.other_state) }}
          />
          <span className="flex-none text-muted">{t(roleLabelKey(l))}</span>
          <span className="flex-none font-mono tabular-nums text-accent">
            {l.other_tn ?? l.other_ticket_id}
          </span>
          <span className="min-w-0 max-w-[28ch] truncate text-ink">{l.other_title}</span>
          <span className="flex-none text-muted">· {stateLabel(t, l.other_state)}</span>
        </Link>
      ))}
    </div>
  );
}
