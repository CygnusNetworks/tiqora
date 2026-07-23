import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import { TicketHeader } from "@/components/agent/TicketHeader";
import { ArticleMasterDetail } from "@/components/agent/ArticleMasterDetail";
import { HistoryTable } from "@/components/agent/HistoryTable";
import { PresenceBar } from "@/components/agent/PresenceBar";
import { ProcessWidget } from "@/components/agent/process/ProcessWidget";
import { AiPanel } from "@/components/agent/AiPanel";
import { TicketZoomOverflowMenu } from "@/components/agent/TicketZoomOverflowMenu";
import { Spinner } from "@/components/ui/Spinner";

// Sliding presence renewal: comfortably inside the backend's 30s TTL (see
// POST /api/v1/tickets/{id}/presence) so a normal heartbeat cadence never
// lets the entry expire while the agent is still on the page.
const PRESENCE_HEARTBEAT_MS = 20000;

export function TicketZoomPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { ticketId: ticketIdStr } = useParams({
    from: "/agent/tickets/$ticketId",
  });
  const ticketId = Number(ticketIdStr);
  const [tab, setTab] = useState<"articles" | "history">("articles");
  const [composing, setComposing] = useState(false);
  // Sort state lives here so the ⋮ menu can toggle it for either view.
  const [articleDescending, setArticleDescending] = useState(true);
  const [historyOrder, setHistoryOrder] = useState<"asc" | "desc">("desc");
  const [noteOpen, setNoteOpen] = useState(false);
  const [processStartOpen, setProcessStartOpen] = useState(false);
  const validTicketId = Number.isFinite(ticketId) && ticketId > 0;

  const ticketQ = useQuery({
    queryKey: ["tickets", ticketId],
    queryFn: () => api.getTicket(ticketId),
    enabled: validTicketId,
  });

  const processStateQ = useQuery({
    queryKey: ["process", "ticket", ticketId, "state"],
    queryFn: ({ signal }) => api.getTicketProcessState(ticketId, signal),
    enabled: validTicketId,
  });

  // Presence heartbeat: announce viewing/composing on mount, on mode
  // change, and on an interval well under the 30s server-side TTL.
  useEffect(() => {
    if (!validTicketId) return undefined;
    const mode = composing ? "composing" : "viewing";
    const post = () => {
      void api.postPresence(ticketId, { mode }).catch(() => {
        // Best-effort — presence is a nice-to-have, not a critical write.
      });
    };
    post();
    const interval = window.setInterval(post, PRESENCE_HEARTBEAT_MS);
    return () => window.clearInterval(interval);
  }, [ticketId, validTicketId, composing]);

  if (!validTicketId) {
    return <p className="p-6 text-sm text-danger">{t("ticket.invalidId")}</p>;
  }

  if (ticketQ.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }

  if (ticketQ.isError || !ticketQ.data) {
    return (
      <div className="p-6">
        <p className="text-danger">{t("ticket.loadError")}</p>
        <Link to="/agent/queues" className="mt-2 inline-block text-sm text-accent">
          {t("common.back")}
        </Link>
      </div>
    );
  }

  const canStartProcess =
    !processStateQ.isLoading &&
    !processStateQ.isError &&
    !processStateQ.data?.process_entity_id;

  const sortLabel =
    tab === "articles"
      ? articleDescending
        ? t("ticket.sortNewestFirst")
        : t("ticket.sortOldestFirst")
      : historyOrder === "desc"
        ? t("ticket.historySortDesc")
        : t("ticket.historySortAsc");

  const onToggleSort = () => {
    if (tab === "articles") {
      setArticleDescending((d) => !d);
    } else {
      setHistoryOrder((o) => (o === "desc" ? "asc" : "desc"));
    }
  };

  // note permission: prefer explicit permissions.note; fall back to can_write.
  const canNote = ticketQ.data
    ? ticketQ.data.permissions
      ? Boolean(ticketQ.data.permissions.note || ticketQ.data.permissions.rw)
      : Boolean(ticketQ.data.can_write)
    : false;
  // Deleting a note is destructive — gate on ``rw`` specifically, not the
  // weaker ``note`` permission that's enough to create one.
  const canDeleteNote = Boolean(ticketQ.data?.permissions?.rw);

  const overflowMenu = (
    <TicketZoomOverflowMenu
      tab={tab}
      onTabChange={(next) => {
        setTab(next);
        // Opening history while composing a note: leave the note alone.
      }}
      sortLabel={sortLabel}
      onToggleSort={onToggleSort}
      onInternalNote={() => {
        setTab("articles");
        setNoteOpen(true);
      }}
      canNote={canNote}
      canStartProcess={Boolean(canStartProcess)}
      onStartProcess={() => setProcessStartOpen(true)}
    />
  );

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 px-4 py-4" data-testid="ticket-zoom">
      <Link to="/agent/queues" className="text-xs text-accent hover:underline">
        ← {t("common.backToQueues")}
      </Link>
      {/* Ticket info + primary actions/pills, then content. */}
      <TicketHeader
        ticket={ticketQ.data}
        overflowMenu={overflowMenu}
        canNote={canNote}
        onOpenNote={() => setNoteOpen(true)}
      />
      <ProcessWidget
        ticketId={ticketId}
        hideInactiveStart
        startOpen={processStartOpen}
        onStartOpenChange={setProcessStartOpen}
      />
      <AiPanel ticketId={ticketId} canNote={canNote} />
      <PresenceBar ticketId={ticketId} selfUserId={user?.id} />
      {tab === "articles" ? (
        <ArticleMasterDetail
          ticketId={ticketId}
          onComposingChange={setComposing}
          descending={articleDescending}
          onToggleDescending={() => setArticleDescending((d) => !d)}
          noteOpen={noteOpen}
          onNoteOpenChange={setNoteOpen}
          canNote={canNote}
          canDelete={canDeleteNote}
        />
      ) : (
        <HistoryTable ticketId={ticketId} order={historyOrder} />
      )}
    </div>
  );
}
