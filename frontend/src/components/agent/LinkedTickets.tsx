import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { LinkGraph } from "@/components/itsm/LinkGraph";
import { LinkedNoteDialog } from "@/components/itsm/LinkedNoteDialog";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { api } from "@/lib/api";
import { itsmApi, itsmKeys } from "@/lib/itsmApi";
import { stateColorVar, stateLabel } from "@/lib/status";

type TicketLink = Awaited<ReturnType<typeof api.listTicketLinks>>[number];

function roleLabelKey(link: TicketLink): string {
  if (link.other_role === "parent") return "ticket.links.parent";
  if (link.other_role === "child") return "ticket.links.child";
  return "ticket.links.related";
}

/**
 * "Linked tickets" row of the ticket-zoom header: one chip per link with the
 * relation (parent / child / related), number, title and state of the other
 * ticket. Shares its query key with the link dialog, so adding a link there
 * shows up here without a reload. "Notiz an verknüpfte Tickets" and the graph
 * are general ticket features and stay in every ITSM mode. Renders nothing when
 * the ticket has neither ticket nor CI links.
 */
export function LinkedTickets({
  ticketId,
  ticketTn,
  canWrite = true,
}: {
  ticketId: number;
  ticketTn?: string;
  /** `rw` on the ticket: removing a link and the linked note need it. */
  canWrite?: boolean;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [removing, setRemoving] = useState<TicketLink | null>(null);
  const [noteOpen, setNoteOpen] = useState(false);
  const [showGraph, setShowGraph] = useState(false);
  // Shared with the ITSM chips: a ticket with only CI links still offers the graph.
  const itsmQ = useQuery({
    queryKey: itsmKeys.ticketItsm(ticketId),
    queryFn: ({ signal }) => itsmApi.ticketItsm(ticketId, signal),
    retry: false,
  });
  const ciCount = itsmQ.data?.ci_count ?? 0;
  const linksQ = useQuery({
    queryKey: ["tickets", ticketId, "links"],
    queryFn: () => api.listTicketLinks(ticketId),
  });
  const remove = useMutation({
    mutationFn: (l: TicketLink) => itsmApi.unlinkTicket(ticketId, l.other_ticket_id, l.link_type),
    onSuccess: () => {
      setRemoving(null);
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId] });
    },
  });
  const links = linksQ.data ?? [];
  if (links.length === 0 && ciCount === 0) return null;

  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[12.5px]"
      data-testid="linked-tickets"
    >
      <span className="text-muted">{t("ticket.links.label")}</span>
      {links.map((l) => (
        <span key={`${l.other_ticket_id}-${l.link_type}`} className="inline-flex min-w-0 max-w-full items-center gap-0.5">
        <Link
          to="/agent/tickets/$ticketId"
          params={{ ticketId: String(l.other_ticket_id) }}
          className="inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-md border border-hairline bg-surface-subtle px-2 py-0.5 transition-colors duration-100 hover:border-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          title={`${l.other_tn ?? l.other_ticket_id} — ${l.other_title ?? ""}`}
          data-testid={`linked-ticket-${l.other_ticket_id}`}
        >
          <span
            aria-hidden
            className="h-2 w-2 flex-none rounded-full"
            style={{ background: stateColorVar(l.other_state) }}
          />
          <span className="flex-none text-muted">{t(roleLabelKey(l))}</span>
          <span className="flex-none font-mono tabular-nums text-accent">
            {l.other_tn ?? l.other_ticket_id}
          </span>
          <span className="min-w-0 max-w-[28ch] truncate text-ink">{l.other_title}</span>
          <span className="flex-none text-muted">· {stateLabel(t, l.other_state)}</span>
        </Link>
        {canWrite && (
          <button
            type="button"
            className="flex-none rounded px-1 text-muted hover:text-danger focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            aria-label={t("ticket.links.remove")}
            title={t("ticket.links.remove")}
            onClick={() => setRemoving(l)}
            data-testid={`linked-ticket-remove-${l.other_ticket_id}`}
          >
            ×
          </button>
        )}
        </span>
      ))}
      {canWrite && links.length > 0 && (
        <button
          type="button"
          className="text-accent hover:underline"
          onClick={() => setNoteOpen(true)}
          data-testid="linked-note-open"
        >
          {t("itsm.linkedNote.open")}
        </button>
      )}
      <button
        type="button"
        className="text-accent hover:underline"
        aria-pressed={showGraph}
        onClick={() => setShowGraph((v) => !v)}
        data-testid="link-graph-toggle"
      >
        {t("itsm.graph.toggle")}
      </button>
      {showGraph && (
        <div className="w-full basis-full">
          <LinkGraph ticketId={ticketId} />
        </div>
      )}
      {removing && (
        <ConfirmDialog
          open
          role="alertdialog"
          variant="danger"
          title={t("ticket.links.removeTitle")}
          message={t("ticket.links.removeConfirm", {
            a: ticketTn ?? ticketId,
            b: removing.other_tn ?? removing.other_ticket_id,
          })}
          confirmLabel={t("ticket.links.remove")}
          pending={remove.isPending}
          onConfirm={() => remove.mutate(removing)}
          onCancel={() => setRemoving(null)}
        >
          {remove.isError && (
            <p className="text-xs text-danger" role="alert" data-testid="link-remove-error">
              {t("ticket.links.removeError")}
            </p>
          )}
        </ConfirmDialog>
      )}
      {noteOpen && (
        <LinkedNoteDialog
          ticketId={ticketId}
          targets={[...new Map(links.map((l) => [l.other_ticket_id, l])).values()].map((l) => ({
            id: l.other_ticket_id,
            tn: l.other_tn ?? String(l.other_ticket_id),
            title: l.other_title ?? "",
          }))}
          onClose={() => setNoteOpen(false)}
        />
      )}
    </div>
  );
}
