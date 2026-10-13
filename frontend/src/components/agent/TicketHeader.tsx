import type { TicketDetail } from "@/lib/api";
import { TicketItsmChips } from "@/components/itsm/TicketItsmChips";
import { LinkedTickets } from "./LinkedTickets";
import { TicketDynamicFields } from "./TicketDynamicFields";
import { TicketHeaderActions } from "./TicketHeaderActions";
import type { TicketAiSlots } from "./AiPanel";
import type { AllowedActions } from "@/lib/aclActions";
import { ticketPerms } from "@/lib/ticket";
import { combinedEscalationLevel, spineClassName, stateColorVar } from "@/lib/status";
import type { CSSProperties, ReactNode } from "react";

/**
 * Ticket-zoom header shell: escalation/state spine + the content rows (see
 * `TicketHeaderActions`, which owns the reference-data queries and dialog
 * wiring and places the AI pieces) + collapsible, editable dynamic fields (`TicketDynamicFields`).
 */
export function TicketHeader({
  ticket,
  overflowItems,
  canNote,
  onOpenNote,
  ai,
  similar,
  allowedActions = null,
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
  /** Action ACL result for this ticket (`null` = unknown: show everything). */
  allowedActions?: AllowedActions;
}) {
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
        allowedActions={allowedActions}
      />
      <LinkedTickets ticketId={ticket.id} ticketTn={ticket.tn} canWrite={ticketPerms(ticket).rw} />
      <TicketItsmChips ticket={ticket} allowedActions={allowedActions} />
      <TicketDynamicFields ticket={ticket} allowedActions={allowedActions} />
    </header>
  );
}
