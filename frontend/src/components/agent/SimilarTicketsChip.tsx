import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { StateChip } from "@/components/ui/StatusChip";
import { HoverCard } from "@/components/ui/HoverCard";
import { StackIcon } from "@/components/ui/icons";
import { AssistChip, ChipCount } from "./AssistChip";

const similarKey = (ticketId: number) => ["tickets", ticketId, "similar"] as const;

/**
 * "Similar closed tickets" chip for the ticket header's assist row. The
 * Meili query runs only once the card first opens (hover or click); the
 * chip shows the hit count from then on.
 */
export function SimilarTicketsChip({
  ticketId,
  variant = "chip",
}: {
  ticketId: number;
  /** "link": plain text trigger for the ticket header's people row. */
  variant?: "chip" | "link";
}) {
  const { t } = useTranslation();
  // Reads the cache the card's list fills — never fetches by itself.
  const cached = useQuery({
    queryKey: similarKey(ticketId),
    queryFn: ({ signal }) => api.getSimilarTickets(ticketId, signal),
    enabled: false,
  });
  const count = cached.data?.items.length;

  return (
    <HoverCard
      label={t("ticket.similar.title")}
      panelTestId="similar-tickets-body"
      trigger={({ ref, triggerProps }) =>
        variant === "link" ? (
          <button
            ref={ref}
            type="button"
            {...triggerProps}
            data-testid="similar-tickets-toggle"
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium text-accent transition-colors hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          >
            <StackIcon className="h-3.5 w-3.5" />
            {t("ticket.similar.title")}
            {count !== undefined && <span className="font-mono tabular-nums">({count})</span>}
          </button>
        ) : (
          <AssistChip ref={ref} {...triggerProps} data-testid="similar-tickets-toggle">
            <StackIcon className="h-3.5 w-3.5 text-muted" />
            {t("ticket.similar.short")}
            {count !== undefined && <ChipCount value={count} />}
          </AssistChip>
        )
      }
    >
      <div className="space-y-2">
        <div className="text-xs uppercase tracking-wide text-muted">
          {t("ticket.similar.title")}
        </div>
        <SimilarTicketsList ticketId={ticketId} />
      </div>
    </HoverCard>
  );
}

function SimilarTicketsList({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const similarQ = useQuery({
    queryKey: similarKey(ticketId),
    queryFn: ({ signal }) => api.getSimilarTickets(ticketId, signal),
    enabled: ticketId > 0,
  });

  if (similarQ.isLoading) {
    return (
      <div className="flex justify-center py-3" data-testid="similar-tickets-loading">
        <Spinner />
      </div>
    );
  }
  if (similarQ.isError) {
    return (
      <p className="text-xs text-danger" data-testid="similar-tickets-error">
        {t("ticket.similar.loadError")}
      </p>
    );
  }
  if (!similarQ.data || similarQ.data.items.length === 0) {
    return (
      <p className="text-xs text-muted" data-testid="similar-tickets-empty">
        {t("ticket.similar.empty")}
      </p>
    );
  }
  return (
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
            {item.queue_name && <span className="text-xs text-muted">{item.queue_name}</span>}
            <span className="w-full truncate text-ink sm:w-auto sm:flex-1">
              {item.title || t("ticket.noTitle")}
            </span>
            {item.score > 0 && (
              <span className="text-xs text-muted" data-testid={`similar-tickets-score-${item.id}`}>
                {Math.round(item.score * 100)}%
              </span>
            )}
          </Link>
        </li>
      ))}
    </ul>
  );
}
