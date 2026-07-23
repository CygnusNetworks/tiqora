import { useTranslation } from "react-i18next";
import type { TicketDetail } from "@/lib/api";
import { TicketHeaderActions } from "./TicketHeaderActions";
import { combinedEscalationLevel, spineClassName, stateColorVar } from "@/lib/status";
import type { CSSProperties, ReactNode } from "react";

/**
 * Ticket-zoom header shell: escalation/state spine + the "Variante 2 — Zwei
 * Ebenen" content rows (see `TicketHeaderActions`) + collapsible dynamic
 * fields. Number/queue/title/SLA/people all live in the actions component,
 * which owns the reference-data queries and dialog wiring.
 */
export function TicketHeader({
  ticket,
  overflowMenu,
  canNote,
  onOpenNote,
}: {
  ticket: TicketDetail;
  /** Optional ⋮ overflow menu rendered at the end of the actions row. */
  overflowMenu?: ReactNode;
  /** Whether the agent may reply / add notes (``note`` permission). */
  canNote: boolean;
  /** Opens the internal-note composer at the bottom of the article list. */
  onOpenNote: () => void;
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
      className={`space-y-2 rounded-lg border border-hairline bg-surface p-3.5 pl-5 ${spineClassName(
        escLevel,
      )}`}
      style={{ "--spine-color": spineColor } as CSSProperties}
      data-testid="ticket-header"
    >
      <TicketHeaderActions
        ticket={ticket}
        canNote={canNote}
        onOpenNote={onOpenNote}
        overflowMenu={overflowMenu}
      />
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
