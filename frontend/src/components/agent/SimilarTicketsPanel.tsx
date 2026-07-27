import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { StateChip } from "@/components/ui/StatusChip";

/**
 * Collapsible "similar closed tickets" panel for TicketZoom.
 * Default closed — Meili query runs only after the agent expands it.
 */
export function SimilarTicketsPanel({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  const similarQ = useQuery({
    queryKey: ["tickets", ticketId, "similar"],
    queryFn: ({ signal }) => api.getSimilarTickets(ticketId, signal),
    enabled: expanded && ticketId > 0,
  });

  return (
    <div
      className="rounded-lg border border-hairline bg-surface"
      data-testid="similar-tickets-panel"
    >
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        data-testid="similar-tickets-toggle"
      >
        <h2 className="font-display text-sm font-semibold text-ink">
          {t("ticket.similar.title")}
        </h2>
        <span className="text-xs text-muted" aria-hidden>
          {expanded ? "▴" : "▾"}
        </span>
      </button>

      {expanded && (
        <div className="space-y-2 border-t border-hairline px-4 py-3" data-testid="similar-tickets-body">
          {similarQ.isLoading && (
            <div className="flex justify-center py-3" data-testid="similar-tickets-loading">
              <Spinner />
            </div>
          )}
          {similarQ.isError && (
            <p className="text-xs text-danger" data-testid="similar-tickets-error">
              {t("ticket.similar.loadError")}
            </p>
          )}
          {similarQ.data && similarQ.data.items.length === 0 && (
            <p className="text-xs text-muted" data-testid="similar-tickets-empty">
              {t("ticket.similar.empty")}
            </p>
          )}
          {similarQ.data && similarQ.data.items.length > 0 && (
            <ul className="space-y-1.5" data-testid="similar-tickets-list">
              {similarQ.data.items.map((item) => (
                <li key={item.id}>
                  <Link
                    to="/agent/tickets/$ticketId"
                    params={{ ticketId: String(item.id) }}
                    className="flex flex-wrap items-center gap-2 rounded-md border border-transparent px-2 py-1.5 text-sm transition-colors hover:border-hairline hover:bg-surface-subtle"
                    data-testid={`similar-tickets-item-${item.id}`}
                  >
                    <span className="font-mono text-xs text-accent">{item.tn}</span>
                    <StateChip state={item.state} />
                    {item.queue_name && (
                      <span className="text-xs text-muted">{item.queue_name}</span>
                    )}
                    <span className="w-full truncate text-ink sm:w-auto sm:flex-1">
                      {item.title || t("ticket.noTitle")}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
