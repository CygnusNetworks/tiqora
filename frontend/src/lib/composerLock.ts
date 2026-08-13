import { useCallback, useEffect, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type ComposerLockAction = "compose" | "forward" | "bounce";

/**
 * Znuny RequiredLock semantics for the composer dialogs: opening the dialog
 * acquires the ticket lock (and ownership) server-side. A ticket locked by
 * another agent comes back as `locked_by_other`; the dialog then shows
 * `ComposerLockBanner` and keeps Send disabled until the agent takes over.
 *
 * Closing the dialog deliberately does NOT release the lock — Znuny keeps a
 * composer-opened lock until the unlock timeout or a manual "Freigeben".
 */
export function useComposerLock(
  ticketId: number,
  action: ComposerLockAction,
  open: boolean,
) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (takeover: boolean) =>
      api.acquireTicketLock(ticketId, { action, takeover }),
    onSuccess: (res) => {
      if (res.result === "acquired" || res.result === "taken_over") {
        // Lock badge + owner in the header read from the ticket query.
        void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId] });
      }
    },
  });
  const { mutate } = mutation;

  // One acquisition per open transition — not per render while open.
  const openedRef = useRef(false);
  useEffect(() => {
    if (!open) {
      openedRef.current = false;
      return;
    }
    if (openedRef.current) return;
    openedRef.current = true;
    mutate(false);
  }, [open, ticketId, action, mutate]);

  const lockedBy =
    mutation.data?.result === "locked_by_other"
      ? (mutation.data.locked_by_name ?? "?")
      : null;
  const takeOver = useCallback(() => mutate(true), [mutate]);

  return { lockedBy, takeOver, takingOver: mutation.isPending };
}
