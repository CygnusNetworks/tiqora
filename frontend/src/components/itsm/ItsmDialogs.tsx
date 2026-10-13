import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TicketDynamicFields } from "@/components/agent/TicketDynamicFields";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import type { TicketDetail } from "@/lib/api";
import { actionAllowed, type AllowedActions } from "@/lib/aclActions";
import type { TicketItsm } from "@/lib/itsmApi";
import type { DynamicFieldScreen } from "@/lib/phoneApi";
import { ticketPerms } from "@/lib/ticket";

type Which = "itsm_fields" | "decision";

const SCREEN: Record<Which, DynamicFieldScreen> = {
  itsm_fields: "AgentTicketAddtlITSMField",
  decision: "AgentTicketDecision",
};

/**
 * "ITSM-Felder" and "Entscheidung": Znuny's two ITSM screens. Each button opens the dynamic-field
 * editor for its screen in a dialog; saving goes through the ticket PATCH, which refreshes the
 * ticket, the ITSM summary and the priority. A button shows only when the screen has at least one
 * configured field, the agent has `rw` and the screen's action ACL allows it.
 */
export function ItsmDialogs({
  ticket,
  dialogs,
  allowedActions = null,
}: {
  ticket: TicketDetail;
  dialogs: TicketItsm["dialogs"];
  allowedActions?: AllowedActions;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<Which | null>(null);
  const rw = ticketPerms(ticket).rw;
  const buttons = (["itsm_fields", "decision"] as const).filter(
    (w) => dialogs[w] && rw && actionAllowed(allowedActions, SCREEN[w]),
  );
  if (buttons.length === 0) return null;
  const close = () => setOpen(null);

  return (
    <>
      {buttons.map((w) => (
        <Button key={w} size="sm" onClick={() => setOpen(w)} data-testid={`itsm-open-${w}`}>
          {t(`itsm.zoom.dialogs.${w}`)}
        </Button>
      ))}
      {open && (
        <Dialog open onClose={close} title={t(`itsm.zoom.dialogs.${open}`)} size="lg">
          <div data-testid={`itsm-dialog-${open}`}>
            <TicketDynamicFields
              ticket={ticket}
              allowedActions={allowedActions}
              screen={SCREEN[open]}
              embedded
              onSaved={close}
              onCancel={close}
            />
          </div>
        </Dialog>
      )}
    </>
  );
}
