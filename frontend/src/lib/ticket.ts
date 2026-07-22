/** Shared ticket-mutation helpers used by both ActionToolbar (the dialog
 * implementations) and TicketHeaderActions (the pill/menu triggers that
 * reuse those dialogs). Split out of ActionToolbar.tsx so this file only
 * exports non-component functions (keeps react-refresh happy). */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { MutationRequest, TicketDetail } from "@/lib/api";

/** Patch the ticket, then refresh the header/timeline by invalidating it. */
export function usePatchTicket(ticketId: number, onDone?: () => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MutationRequest) => api.patchTicket(ticketId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId] });
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "articles"] });
      onDone?.();
    },
  });
}

/** Resolve per-action flags; fall back to blanket ``can_write`` when the
 * backend has not yet shipped the ``permissions`` object. */
export function ticketPerms(ticket: TicketDetail): {
  ro: boolean;
  move_into: boolean;
  create: boolean;
  note: boolean;
  owner: boolean;
  priority: boolean;
  rw: boolean;
} {
  const p = ticket.permissions;
  if (p) {
    return {
      ro: Boolean(p.ro),
      move_into: Boolean(p.move_into),
      create: Boolean(p.create),
      note: Boolean(p.note),
      owner: Boolean(p.owner),
      priority: Boolean(p.priority),
      rw: Boolean(p.rw),
    };
  }
  const all = Boolean(ticket.can_write);
  return {
    ro: all,
    move_into: all,
    create: all,
    note: all,
    owner: all,
    priority: all,
    rw: all,
  };
}
