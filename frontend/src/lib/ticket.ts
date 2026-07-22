/** Shared ticket-mutation helpers used by both ActionToolbar (the dialog
 * implementations) and TicketHeaderActions (the pill/menu triggers that
 * reuse those dialogs). Split out of ActionToolbar.tsx so this file only
 * exports non-component functions (keeps react-refresh happy). */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { MutationRequest, TicketDetail } from "@/lib/api";

/** Patch the ticket, then refresh everything a mutation can affect: the
 * ticket itself/timeline, any ticket list (queue views), and the sidebar's
 * queue-tree/my-counts badges — a patch can change state, queue or owner,
 * any of which moves the ticket in/out of those counts. ``["tickets"]``
 * covers detail + articles + list queries by prefix (see the queryKey
 * shapes in QueuesPage/AgentShell); ``["queues"]`` is a separate root key
 * so it needs its own invalidation. */
export function usePatchTicket(ticketId: number, onDone?: () => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MutationRequest) => api.patchTicket(ticketId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tickets"] });
      void qc.invalidateQueries({ queryKey: ["queues"] });
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
