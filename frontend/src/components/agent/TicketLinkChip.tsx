import { useTranslation } from "react-i18next";
import type { TicketListItem } from "@/lib/api";
import { SelectMenu } from "@/components/ui/SelectMenu";
import { cn } from "@/lib/cn";

const CHIP =
  "inline-flex flex-none items-center gap-1 rounded px-1.5 font-mono text-[11px] leading-[18px] transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

function LinkIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="11"
      height="11"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M10 13a5 5 0 007 0l3-3a5 5 0 00-7-7l-1 1" />
      <path d="M14 11a5 5 0 00-7 0l-3 3a5 5 0 007 7l1-1" />
    </svg>
  );
}

/**
 * Queue-list marker for a ticket's relations: a link chip (count, or the
 * partner's last digits when there is one) that opens a menu of the linked
 * tickets, and for a merged ticket a "→ main" chip that jumps to the main
 * ticket. Hovering either highlights the partner rows on the page
 * (`onHover`), which is what makes far-apart rows readable as a pair.
 */
export function TicketLinkChip({
  ticket,
  onHover,
  onOpenTicket,
}: {
  ticket: TicketListItem;
  /** Ids of the rows to highlight while the chip is hovered/focused. */
  onHover: (ids: number[] | null) => void;
  onOpenTicket: (id: number) => void;
}) {
  const { t } = useTranslation();
  const links = ticket.links ?? [];

  if (ticket.merged_into_id != null && ticket.merged_into_tn) {
    const mainId = ticket.merged_into_id;
    const label = t("queue.links.mergedInto", { tn: ticket.merged_into_tn });
    return (
      <button
        type="button"
        className={cn(CHIP, "bg-surface-subtle text-muted hover:text-ink")}
        title={label}
        aria-label={label}
        onClick={(e) => {
          e.stopPropagation();
          onOpenTicket(mainId);
        }}
        onKeyDown={(e) => e.stopPropagation()}
        onMouseEnter={() => onHover([mainId])}
        onMouseLeave={() => onHover(null)}
        onFocus={() => onHover([mainId])}
        onBlur={() => onHover(null)}
        data-testid={`ticket-merged-chip-${ticket.id}`}
      >
        → {ticket.merged_into_tn.slice(-3)}
        <span className="font-sans text-[10.5px] font-normal">{t("queue.links.merged")}</span>
      </button>
    );
  }
  if (links.length === 0) return null;

  const roleLabel = (role: string | null | undefined) =>
    role === "parent"
      ? t("ticket.links.parent")
      : role === "child"
        ? t("ticket.links.child")
        : t("ticket.links.related");
  const ids = links.map((l) => l.ticket_id);
  const label = t("queue.links.count", { count: links.length });
  return (
    <SelectMenu
      items={links.map((l) => ({
        value: l.ticket_id,
        label: `${l.tn} · ${roleLabel(l.role)}`,
      }))}
      onSelect={onOpenTicket}
      searchThreshold={99}
      trigger={({ ref, toggleProps }) => (
        <button
          ref={ref}
          type="button"
          className={cn(CHIP, "bg-accent-dim text-accent hover:ring-1 hover:ring-accent")}
          title={label}
          aria-label={label}
          aria-haspopup={toggleProps["aria-haspopup"]}
          aria-expanded={toggleProps["aria-expanded"]}
          onClick={(e) => {
            e.stopPropagation();
            toggleProps.onClick();
          }}
          onKeyDown={(e) => {
            e.stopPropagation();
            toggleProps.onKeyDown(e);
          }}
          onMouseEnter={() => onHover(ids)}
          onMouseLeave={() => onHover(null)}
          onFocus={() => onHover(ids)}
          onBlur={() => onHover(null)}
          data-testid={`ticket-links-chip-${ticket.id}`}
        >
          <LinkIcon />
          {links.length === 1 ? `…${links[0].tn.slice(-3)}` : links.length}
        </button>
      )}
    />
  );
}
