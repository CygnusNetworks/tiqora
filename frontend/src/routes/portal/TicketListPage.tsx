import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch, Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { portalApi, type TicketListItem } from "@/lib/portalApi";
import { stateColorVar } from "@/lib/status";
import { formatDateTime } from "@/lib/format";
import { Tabs } from "@/components/ui/Tabs";
import { Spinner } from "@/components/ui/Spinner";
import { Button } from "@/components/ui/Button";

const STATE_TABS = ["all", "open", "pending", "closed"] as const;
type StateTab = (typeof STATE_TABS)[number];

export type PortalTicketListSearch = {
  state_type?: StateTab;
};

export function TicketListPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const navigate = useNavigate({ from: "/portal" });
  const search = useSearch({ from: "/portal/" }) as PortalTicketListSearch;
  const stateType = search.state_type ?? "all";

  const ticketsQ = useQuery({
    queryKey: ["portal", "tickets"],
    queryFn: () => portalApi.portalListTickets({ limit: 100 }),
  });

  const items = ticketsQ.data?.items;
  const filtered = useMemo(() => {
    const all = items ?? [];
    if (stateType === "all") return all;
    return all.filter((it) => (it.state_type ?? "").toLowerCase() === stateType);
  }, [items, stateType]);

  const setStateType = (id: string) => {
    void navigate({ search: { state_type: id as StateTab }, replace: true });
  };

  return (
    <div className="space-y-4" data-testid="portal-ticket-list-page">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("portal.tickets.title")}
        </h1>
        <Link to="/portal/tickets/new">
          <Button variant="primary" size="sm" data-testid="portal-new-ticket-link">
            {t("portal.nav.newTicket")}
          </Button>
        </Link>
      </div>

      <Tabs
        value={stateType}
        onChange={setStateType}
        items={STATE_TABS.map((id) => ({
          id,
          label: t(`portal.tickets.stateTabs.${id}`),
        }))}
      />

      {ticketsQ.isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState hasAnyTickets={(items?.length ?? 0) > 0} />
      ) : (
        <ul className="space-y-2" data-testid="portal-ticket-list">
          {filtered.map((tk) => (
            <TicketRow key={tk.id} ticket={tk} locale={locale} />
          ))}
        </ul>
      )}
    </div>
  );
}

function TicketRow({ ticket, locale }: { ticket: TicketListItem; locale: string }) {
  const { t } = useTranslation();
  const spineColor = stateColorVar(ticket.state);
  return (
    <li>
      <Link
        to="/portal/tickets/$ticketId"
        params={{ ticketId: String(ticket.id) }}
        className="status-spine flex flex-col gap-1 rounded-lg border border-hairline bg-surface px-4 py-3 pl-5 transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        style={{ "--spine-color": spineColor } as React.CSSProperties}
        data-testid={`portal-ticket-${ticket.id}`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs tabular-nums text-muted">{ticket.tn}</span>
          <span
            className="rounded border border-hairline px-1.5 py-0.5 text-[11px] font-medium capitalize text-ink"
            style={{ borderColor: spineColor, color: spineColor }}
          >
            {ticket.state || t("portal.tickets.unknownState")}
          </span>
        </div>
        <p className="truncate text-sm font-medium text-ink">
          {ticket.title || t("ticket.noTitle")}
        </p>
        <p className="text-xs text-muted">
          {t("portal.tickets.updated", { date: formatDateTime(ticket.change_time, locale) })}
        </p>
      </Link>
    </li>
  );
}

function EmptyState({ hasAnyTickets }: { hasAnyTickets: boolean }) {
  const { t } = useTranslation();
  return (
    <div
      className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-10 text-center"
      data-testid="portal-ticket-list-empty"
    >
      <p className="text-sm font-medium text-ink">
        {hasAnyTickets
          ? t("portal.tickets.emptyFiltered")
          : t("portal.tickets.emptyNone")}
      </p>
      <p className="mt-1 text-sm text-muted">{t("portal.tickets.emptyHint")}</p>
      <Link
        to="/portal/tickets/new"
        className="mt-4 inline-block text-sm text-accent hover:underline"
      >
        {t("portal.nav.newTicket")}
      </Link>
    </div>
  );
}
