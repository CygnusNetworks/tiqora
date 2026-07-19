import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import { TicketHeader } from "@/components/agent/TicketHeader";
import { ArticleTimeline } from "@/components/agent/ArticleTimeline";
import { HistoryTable } from "@/components/agent/HistoryTable";
import { PresenceBar } from "@/components/agent/PresenceBar";
import { Tabs } from "@/components/ui/Tabs";
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
  const validTicketId = Number.isFinite(ticketId) && ticketId > 0;

  const ticketQ = useQuery({
    queryKey: ["tickets", ticketId],
    queryFn: () => api.getTicket(ticketId),
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

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 px-4 py-4" data-testid="ticket-zoom">
      <Link to="/agent/queues" className="text-xs text-accent hover:underline">
        ← {t("common.backToQueues")}
      </Link>
      <TicketHeader ticket={ticketQ.data} />
      <PresenceBar ticketId={ticketId} selfUserId={user?.id} />
      <Tabs
        value={tab}
        onChange={(id) => setTab(id as "articles" | "history")}
        items={[
          { id: "articles", label: t("ticket.articles") },
          { id: "history", label: t("ticket.history") },
        ]}
      />
      {tab === "articles" ? (
        <ArticleTimeline ticketId={ticketId} onComposingChange={setComposing} />
      ) : (
        <HistoryTable ticketId={ticketId} />
      )}
    </div>
  );
}
