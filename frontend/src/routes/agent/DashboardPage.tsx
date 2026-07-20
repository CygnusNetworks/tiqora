import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { TicketListItem } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import { flattenQueues } from "@/components/agent/QueueTree";
import { QueueShortcutCard } from "@/components/agent/dashboard/QueueShortcutCard";
import { DashboardTicketRow } from "@/components/agent/dashboard/DashboardTicketRow";
import { StatTile } from "@/components/agent/stats/StatTile";
import { Spinner } from "@/components/ui/Spinner";
import { formatDateTime } from "@/lib/format";
import { escalationLevel, formatCountdown } from "@/lib/status";
import { cn } from "@/lib/cn";

/** Smallest escalation epoch that is actually set (>0), past or future. */
function nearestEscalation(t: TicketListItem): number | null {
  const epochs = [
    t.escalation_time,
    t.escalation_response_time,
    t.escalation_update_time,
    t.escalation_solution_time,
  ].filter((e): e is number => typeof e === "number" && e > 0);
  return epochs.length ? Math.min(...epochs) : null;
}

const escalationToneClass: Record<string, string> = {
  breached: "text-danger",
  approaching: "text-warn",
  none: "text-muted",
};

/** KPI tile that scroll-anchors to the dashboard widget listing its tickets. */
function KpiTile({
  anchor,
  label,
  value,
  tone,
  testId,
}: {
  anchor: string;
  label: string;
  value: number;
  tone?: "default" | "danger" | "warn";
  testId: string;
}) {
  return (
    <a
      href={`#${anchor}`}
      className="block rounded-lg transition-shadow hover:ring-1 hover:ring-accent/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
    >
      <StatTile label={label} value={value} tone={tone} testId={testId} />
    </a>
  );
}

/** Section wrapper for a work list: heading, optional "view all" link, body. */
function WidgetCard({
  id,
  title,
  action,
  children,
}: {
  id: string;
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h2>
        {action}
      </div>
      <div className="overflow-hidden rounded-lg border border-hairline bg-surface">{children}</div>
    </section>
  );
}

function EmptyRow({ label }: { label: string }) {
  return <p className="px-4 py-6 text-sm text-muted">{label}</p>;
}

export function DashboardPage() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const nowSeconds = Date.now() / 1000;

  const queuesQ = useQuery({
    queryKey: ["queues"],
    queryFn: () => api.listQueues(),
  });

  const summaryQ = useQuery({
    queryKey: ["tickets", "dashboard-summary"],
    queryFn: () => api.dashboardSummary(),
  });

  const lockedQ = useQuery({
    queryKey: ["tickets", "my-locked", user?.id],
    queryFn: () =>
      api.listTickets({
        owner_id: user?.id,
        state_type: "open",
        limit: 8,
        sort: "age",
        order: "desc",
      }),
    enabled: Boolean(user?.id),
  });

  // Unclaimed new tickets = still owned by root (owner_id=1), oldest first.
  const unownedQ = useQuery({
    queryKey: ["tickets", "unowned-new"],
    queryFn: () =>
      api.listTickets({
        state_type: "new",
        owner_id: 1,
        sort: "age",
        order: "asc",
        limit: 8,
      }),
  });

  // A page of viewable open tickets; the soonest escalations are picked out
  // client-side (list_tickets has no sort=escalation).
  const openForEscQ = useQuery({
    queryKey: ["tickets", "open-page"],
    queryFn: () =>
      api.listTickets({ state_type: "open", sort: "age", order: "asc", limit: 50 }),
  });

  // My pending tickets, for the reminders-due widget.
  const pendingQ = useQuery({
    queryKey: ["tickets", "my-pending", user?.id],
    queryFn: () =>
      api.listTickets({
        owner_id: user?.id,
        state_type: "pending",
        sort: "age",
        order: "asc",
        limit: 20,
      }),
    enabled: Boolean(user?.id),
  });

  const flat = flattenQueues(queuesQ.data ?? []);
  const topQueues = flat
    .slice()
    .sort((a, b) => (b.counts?.open ?? 0) - (a.counts?.open ?? 0))
    .slice(0, 8);

  const escalating = (openForEscQ.data?.items ?? [])
    .map((ticket) => ({ ticket, epoch: nearestEscalation(ticket) }))
    .filter((x): x is { ticket: TicketListItem; epoch: number } => x.epoch != null)
    .sort((a, b) => a.epoch - b.epoch)
    .slice(0, 6);

  // Reminders due: overdue or due within the next 24h.
  const remindersDue = (pendingQ.data?.items ?? [])
    .filter((ticket) => ticket.until_time > 0 && ticket.until_time <= nowSeconds + 86400)
    .sort((a, b) => a.until_time - b.until_time)
    .slice(0, 6);

  const summary = summaryQ.data;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-8 px-4 py-6" data-testid="dashboard">
      <div>
        <h1 className="font-display text-2xl font-semibold text-ink">{t("dashboard.title")}</h1>
        <p className="mt-1 text-sm text-muted">
          {t("dashboard.welcome", { name: user?.first_name || user?.login || "" })}
        </p>
      </div>

      {/* KPI tiles — each jumps to the widget that lists its tickets. */}
      <section aria-label={t("dashboard.kpiHeading")}>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <KpiTile
            anchor="widget-my-tickets"
            label={t("dashboard.kpiMyOpen")}
            value={summary?.my_open ?? 0}
            testId="kpi-my-open"
          />
          <KpiTile
            anchor="widget-my-tickets"
            label={t("dashboard.kpiMyNew")}
            value={summary?.my_new ?? 0}
            testId="kpi-my-new"
          />
          <KpiTile
            anchor="widget-to-pick-up"
            label={t("dashboard.kpiUnownedNew")}
            value={summary?.unowned_new ?? 0}
            testId="kpi-unowned-new"
          />
          <KpiTile
            anchor="widget-escalating"
            label={t("dashboard.kpiEscalated")}
            value={summary?.escalated ?? 0}
            tone={(summary?.escalated ?? 0) > 0 ? "danger" : "default"}
            testId="kpi-escalated"
          />
        </div>
      </section>

      {/* Work widgets */}
      <div className="grid gap-6 lg:grid-cols-2">
        <WidgetCard id="widget-my-tickets" title={t("dashboard.myTickets")}>
          {lockedQ.isLoading ? (
            <Spinner />
          ) : (lockedQ.data?.items ?? []).length === 0 ? (
            <EmptyRow label={t("dashboard.noMyTickets")} />
          ) : (
            <ul className="divide-y divide-hairline">
              {(lockedQ.data?.items ?? []).map((ticket) => (
                <DashboardTicketRow
                  key={ticket.id}
                  ticket={ticket}
                  trailing={formatDateTime(ticket.change_time, locale)}
                />
              ))}
            </ul>
          )}
        </WidgetCard>

        <WidgetCard
          id="widget-to-pick-up"
          title={t("dashboard.toPickUp")}
          action={
            <Link
              to="/agent/queues"
              search={{ state_type: "new" }}
              className="text-xs text-accent hover:underline"
            >
              {t("dashboard.viewAll")}
            </Link>
          }
        >
          {unownedQ.isLoading ? (
            <Spinner />
          ) : (unownedQ.data?.items ?? []).length === 0 ? (
            <EmptyRow label={t("dashboard.toPickUpEmpty")} />
          ) : (
            <ul className="divide-y divide-hairline">
              {(unownedQ.data?.items ?? []).map((ticket) => (
                <DashboardTicketRow
                  key={ticket.id}
                  ticket={ticket}
                  trailing={formatDateTime(ticket.create_time, locale)}
                />
              ))}
            </ul>
          )}
        </WidgetCard>

        <WidgetCard id="widget-escalating" title={t("dashboard.escalatingSoon")}>
          {openForEscQ.isLoading ? (
            <Spinner />
          ) : escalating.length === 0 ? (
            <EmptyRow label={t("dashboard.escalatingSoonEmpty")} />
          ) : (
            <ul className="divide-y divide-hairline">
              {escalating.map(({ ticket, epoch }) => (
                <DashboardTicketRow
                  key={ticket.id}
                  ticket={ticket}
                  trailing={
                    <span className={escalationToneClass[escalationLevel(epoch)]}>
                      {formatCountdown(epoch)}
                    </span>
                  }
                />
              ))}
            </ul>
          )}
        </WidgetCard>

        <WidgetCard id="widget-reminders" title={t("dashboard.remindersDue")}>
          {pendingQ.isLoading ? (
            <Spinner />
          ) : remindersDue.length === 0 ? (
            <EmptyRow label={t("dashboard.remindersDueEmpty")} />
          ) : (
            <ul className="divide-y divide-hairline">
              {remindersDue.map((ticket) => {
                const overdue = ticket.until_time <= nowSeconds;
                return (
                  <DashboardTicketRow
                    key={ticket.id}
                    ticket={ticket}
                    trailing={
                      <span className={cn(overdue && "text-danger")}>
                        {formatDateTime(new Date(ticket.until_time * 1000), locale)}
                      </span>
                    }
                  />
                );
              })}
            </ul>
          )}
        </WidgetCard>
      </div>

      {/* Queue shortcuts */}
      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
            {t("dashboard.queueShortcuts")}
          </h2>
          <Link to="/agent/queues" className="text-xs text-accent hover:underline">
            {t("dashboard.viewAllQueues")}
          </Link>
        </div>
        {queuesQ.isLoading ? (
          <Spinner />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {topQueues.map((q) => (
              <QueueShortcutCard key={q.id} queue={q} />
            ))}
            {topQueues.length === 0 && (
              <p className="col-span-full text-sm text-muted">{t("queue.empty")}</p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
