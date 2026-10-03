import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import type { TicketDetail } from "@/lib/api";
import { navContextFor, neighbours } from "@/lib/ticketNavContext";

const posButton =
  "inline-flex h-6 w-6 items-center justify-center rounded-md border border-hairline bg-surface text-xs text-muted transition-colors duration-100 hover:border-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

/** Ticket number that copies itself to the clipboard on click. */
function CopyTn({ tn }: { tn: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const id = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(id);
  }, [copied]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(tn);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  const label = copied ? t("ticket.nav.tnCopied") : t("ticket.nav.copyTn");
  return (
    <button
      type="button"
      onClick={() => void copy()}
      title={label}
      aria-label={`${label}: ${tn}`}
      className="group inline-flex items-center gap-1 rounded font-mono tabular-nums text-muted transition-colors duration-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      data-testid="ticket-back-nav-tn"
    >
      {tn}
      <svg
        viewBox="0 0 24 24"
        width="12"
        height="12"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        className={copied ? "text-accent" : "opacity-60 group-hover:opacity-100"}
      >
        {copied ? (
          <path d="M5 12l5 5L20 7" />
        ) : (
          <>
            <rect x="9" y="9" width="11" height="11" rx="2" />
            <path d="M5 15V6a2 2 0 012-2h9" />
          </>
        )}
      </svg>
    </button>
  );
}

/**
 * Breadcrumb back to where the ticket was opened from ("← cn-nord",
 * "← Meine Tickets", "← Suche „…“") plus ‹ › through that same list.
 * Opened without a list (notification, pasted link) it falls back to the
 * ticket's own queue.
 */
export function TicketBackNav({ ticket }: { ticket: TicketDetail }) {
  const { t } = useTranslation();
  const ctx = navContextFor(ticket.id);
  const pos = ctx ? neighbours(ctx, ticket.id) : null;

  return (
    <div className="flex flex-wrap items-center justify-between gap-2" data-testid="ticket-back-nav">
      <nav
        aria-label={t("ticket.nav.label")}
        className="flex min-w-0 flex-wrap items-center gap-1.5 text-[12.5px] text-muted"
      >
        {ctx ? (
          <Link
            // The origin is data (any list route), not a literal route id.
            to={ctx.to as never}
            params={ctx.params as never}
            search={ctx.search as never}
            className="min-w-0 truncate text-accent hover:underline"
            data-testid="ticket-back-link"
          >
            ← {ctx.label}
          </Link>
        ) : (
          <Link
            to="/agent/queues"
            search={{ queue_id: ticket.queue_id }}
            className="min-w-0 truncate text-accent hover:underline"
            data-testid="ticket-back-link"
          >
            ← {t("ticket.queuesRoot")} › {ticket.queue_name || "—"}
          </Link>
        )}
        <span aria-hidden>›</span>
        <CopyTn tn={ticket.tn} />
      </nav>
      {ctx && pos && ctx.ids.length > 1 && (
        <span className="inline-flex items-center gap-1" data-testid="ticket-back-nav-pos">
          <span className="mr-1 font-mono text-[11px] tabular-nums text-muted">
            {t("ticket.nav.position", { index: pos.index + 1, total: ctx.ids.length })}
          </span>
          {pos.prev != null ? (
            <Link
              to="/agent/tickets/$ticketId"
              params={{ ticketId: String(pos.prev) }}
              className={posButton}
              aria-label={t("ticket.nav.prev")}
              title={t("ticket.nav.prev")}
              data-testid="ticket-nav-prev"
            >
              ‹
            </Link>
          ) : (
            <span className={`${posButton} opacity-40`} aria-hidden>
              ‹
            </span>
          )}
          {pos.next != null ? (
            <Link
              to="/agent/tickets/$ticketId"
              params={{ ticketId: String(pos.next) }}
              className={posButton}
              aria-label={t("ticket.nav.next")}
              title={t("ticket.nav.next")}
              data-testid="ticket-nav-next"
            >
              ›
            </Link>
          ) : (
            <span className={`${posButton} opacity-40`} aria-hidden>
              ›
            </span>
          )}
        </span>
      )}
    </div>
  );
}
