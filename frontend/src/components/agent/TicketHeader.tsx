import { useTranslation } from "react-i18next";
import type { TicketDetail } from "@/lib/api";
import { LinkedTickets } from "./LinkedTickets";
import { TicketHeaderActions } from "./TicketHeaderActions";
import type { TicketAiSlots } from "./AiPanel";
import { combinedEscalationLevel, spineClassName, stateColorVar } from "@/lib/status";
import type { CSSProperties, ReactNode } from "react";

/**
 * Ticket-zoom header shell: escalation/state spine + the content rows (see
 * `TicketHeaderActions`, which owns the reference-data queries and dialog
 * wiring and places the AI pieces) + collapsible dynamic fields.
 */
export function TicketHeader({
  ticket,
  overflowItems,
  canNote,
  onOpenNote,
  ai,
  similar,
}: {
  ticket: TicketDetail;
  /** Page-level items appended to the header's ⋯ menu. */
  overflowItems?: ReactNode;
  /** Whether the agent may reply / add notes (``note`` permission). */
  canNote: boolean;
  /** Opens the internal-note composer at the bottom of the article list. */
  onOpenNote: () => void;
  /** AI pieces the header places (summary subtitle, drafts, banners). */
  ai?: TicketAiSlots;
  /** "Similar tickets" trigger for the people row. */
  similar?: ReactNode;
}) {
  const { t } = useTranslation();

  const escLevel = combinedEscalationLevel([
    ticket.escalation_time,
    ticket.escalation_response_time,
    ticket.escalation_update_time,
    ticket.escalation_solution_time,
  ]);
  const spineColor = escLevel === "none" ? stateColorVar(ticket.state) : undefined;

  return (
    <header
      className={`space-y-3 rounded-lg border border-hairline bg-surface p-4 pl-5 ${spineClassName(
        escLevel,
      )}`}
      style={{ "--spine-color": spineColor } as CSSProperties}
      data-testid="ticket-header"
    >
      <TicketHeaderActions
        ticket={ticket}
        canNote={canNote}
        onOpenNote={onOpenNote}
        overflowItems={overflowItems}
        ai={ai}
        similar={similar}
      />
      <LinkedTickets ticketId={ticket.id} />
      {ticket.dynamic_fields && ticket.dynamic_fields.length > 0 && (
        <details className="rounded border border-hairline bg-surface-subtle px-3 py-2 text-sm">
          <summary className="cursor-pointer font-medium text-muted">
            {t("ticket.dynamicFields")}
          </summary>
          <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {ticket.dynamic_fields.map((df) => (
              <div key={df.name}>
                <dt className="text-xs text-muted">{df.label || df.name}</dt>
                <dd className="text-ink">{(df.values ?? []).map(String).join(", ") || "—"}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </header>
  );
}
