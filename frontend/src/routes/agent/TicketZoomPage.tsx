import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { TicketHeader } from "@/components/agent/TicketHeader";
import { ArticleTimeline } from "@/components/agent/ArticleTimeline";
import { HistoryTable } from "@/components/agent/HistoryTable";
import { Tabs } from "@/components/ui/Tabs";
import { Spinner } from "@/components/ui/Spinner";

export function TicketZoomPage() {
  const { t } = useTranslation();
  const { ticketId: ticketIdStr } = useParams({
    from: "/agent/tickets/$ticketId",
  });
  const ticketId = Number(ticketIdStr);
  const [tab, setTab] = useState<"articles" | "history">("articles");

  const ticketQ = useQuery({
    queryKey: ["tickets", ticketId],
    queryFn: () => api.getTicket(ticketId),
    enabled: Number.isFinite(ticketId) && ticketId > 0,
  });

  if (!Number.isFinite(ticketId) || ticketId <= 0) {
    return <p className="p-6 text-danger">{t("ticket.invalidId")}</p>;
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
      <Tabs
        value={tab}
        onChange={(id) => setTab(id as "articles" | "history")}
        items={[
          { id: "articles", label: t("ticket.articles") },
          { id: "history", label: t("ticket.history") },
        ]}
      />
      {tab === "articles" ? (
        <ArticleTimeline ticketId={ticketId} />
      ) : (
        <HistoryTable ticketId={ticketId} />
      )}
    </div>
  );
}
