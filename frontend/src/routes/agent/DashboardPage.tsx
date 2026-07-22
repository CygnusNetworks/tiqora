import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { TicketListItem } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import { flattenQueues } from "@/components/agent/QueueTree";
import { QueueShortcutCard } from "@/components/agent/dashboard/QueueShortcutCard";
import { DashboardTicketRow } from "@/components/agent/dashboard/DashboardTicketRow";
import { Spinner } from "@/components/ui/Spinner";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";

/** Which set of tickets the single dashboard list currently shows. "all"
 * renders the grouped view; everything else is a flat filtered list. */
export type DashboardFilter = "all" | "mine-open" | "mine-new" | "takeover" | "escalated";

const DASHBOARD_FILTER_KEY = "tiqora-dashboard-filter";

function readStoredFilter(): DashboardFilter {
  if (typeof window === "undefined") return "all";
  try {
    const v = window.localStorage.getItem(DASHBOARD_FILTER_KEY);
    return v === "mine-open" || v === "mine-new" || v === "takeover" || v === "escalated"
      ? v
      : "all";
  } catch {
    return "all";
  }
}

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

/** Unsigned "3h20m" duration text for a breached escalation epoch — how long
 * ago it passed, not the signed countdown `formatCountdown` (status.ts) uses
 * for the approaching/breached badge on ticket rows elsewhere. */
export function formatDurationSince(epochSeconds: number): string {
  const diffMs = Math.max(0, Date.now() - epochSeconds * 1000);
  const abs = Math.round(diffMs / 60000);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  return h > 0 ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m`;
}

/** Time-of-day greeting key, German-style tri-split (morning/day/evening). */
export function greetingKey(date = new Date()): "morning" | "day" | "evening" {
  const h = date.getHours();
  if (h < 11) return "morning";
  if (h < 18) return "day";
  return "evening";
}

/** Queues with work: open and/or new tickets (either count > 0). */
export function queueHasWork(queue: {
  counts?: { open?: number; new?: number } | null;
}): boolean {
  return (queue.counts?.open ?? 0) > 0 || (queue.counts?.new ?? 0) > 0;
}

/** Filter and rank queue shortcuts: only non-empty, highest open first, cap at 8. */
export function selectQueueShortcuts<
  T extends { counts?: { open?: number; new?: number } | null },
>(queues: T[], limit = 8): T[] {
  return queues
    .filter(queueHasWork)
    .slice()
    .sort((a, b) => (b.counts?.open ?? 0) - (a.counts?.open ?? 0))
    .slice(0, limit);
}

/** Pill filter chip: label + mono count, accent-filled when active. */
function FilterChip({
  label,
  count,
  active,
  onClick,
  testId,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] font-medium transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
        active
          ? "border-accent bg-accent text-accent-ink"
          : "border-hairline bg-surface text-ink/80 hover:bg-surface-subtle",
      )}
    >
      <span>{label}</span>
      <span
        className={cn(
          "font-mono text-[12px] tabular-nums",
          active ? "text-accent-ink/80" : "text-muted",
        )}
      >
        {count}
      </span>
    </button>
  );
}

/** Permanent red banner shown whenever there is at least one escalated
 * ticket; hidden entirely at zero. Its "Anzeigen" action switches the list
 * below to the escalated view without hiding the strip itself. */
function EscalationStrip({
  count,
  oldestEpoch,
  active,
  onShow,
}: {
  count: number;
  oldestEpoch: number | null;
  active: boolean;
  onShow: () => void;
}) {
  const { t } = useTranslation();
  if (count === 0) return null;
  return (
    <div
      data-testid="dashboard-escalation-strip"
      className={cn(
        "flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger/10 px-4 py-2.5 text-sm text-danger",
        active && "ring-1 ring-danger",
      )}
    >
      <span className="font-medium">
        {t("dashboard.escalationStrip", {
          count,
          duration: oldestEpoch != null ? formatDurationSince(oldestEpoch) : "—",
        })}
      </span>
      <button
        type="button"
        onClick={onShow}
        data-testid="dashboard-escalation-strip-show"
        className="shrink-0 font-medium hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-danger"
      >
        {t("dashboard.escalationShow")}
      </button>
    </div>
  );
}

/** Slim group header used by the grouped ("Alle") ticket list. */
function GroupHeader({
  slug,
  label,
  count,
  action,
}: {
  slug: string;
  label: string;
  count: number;
  action?: ReactNode;
}) {
  return (
    <div
      data-testid={`dashboard-group-${slug}`}
      className="flex items-center justify-between gap-2 bg-surface-subtle px-4 py-2 text-xs font-semibold uppercase tracking-wide text-muted"
    >
      <span>{label}</span>
      <span className="flex items-center gap-2">
        {action}
        <span className="font-mono text-[11px] normal-case tracking-normal tabular-nums">
          {count}
        </span>
      </span>
    </div>
  );
}

function EmptyRow({ label }: { label: string }) {
  return <p className="px-4 py-6 text-sm text-muted">{label}</p>;
}

/** A group's or a single filter's row list: loading / empty / rows. */
function DashboardTicketList({
  items,
  loading,
  emptyLabel,
  trailing,
  escalated,
}: {
  items: TicketListItem[];
  loading: boolean;
  emptyLabel: string;
  trailing: (ticket: TicketListItem) => ReactNode;
  escalated?: boolean;
}) {
  if (loading) return <Spinner />;
  if (items.length === 0) return <EmptyRow label={emptyLabel} />;
  return (
    <ul className="divide-y divide-hairline">
      {items.map((ticket) => (
        <DashboardTicketRow
          key={ticket.id}
          ticket={ticket}
          trailing={trailing(ticket)}
          escalated={escalated}
        />
      ))}
    </ul>
  );
}

export function DashboardPage() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const nowSeconds = Date.now() / 1000;

  const [filter, setFilter] = useState<DashboardFilter>(readStoredFilter);
  useEffect(() => {
    try {
      window.localStorage.setItem(DASHBOARD_FILTER_KEY, filter);
    } catch {
      // best-effort persistence only
    }
  }, [filter]);
  const selectChip = (next: Exclude<DashboardFilter, "all" | "escalated">) =>
    setFilter((prev) => (prev === next ? "all" : next));

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
        limit: 50,
        sort: "age",
        order: "desc",
      }),
    enabled: Boolean(user?.id),
  });

  // My new tickets, for both the "Meine neuen" group and its chip filter.
  const myNewQ = useQuery({
    queryKey: ["tickets", "my-new", user?.id],
    queryFn: () =>
      api.listTickets({
        owner_id: user?.id,
        state_type: "new",
        sort: "age",
        order: "asc",
        limit: 50,
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
        limit: 50,
      }),
  });

  // A page of viewable open tickets; already-breached escalations are picked
  // out client-side (list_tickets has no sort=escalation).
  const openForEscQ = useQuery({
    queryKey: ["tickets", "open-page"],
    queryFn: () => api.listTickets({ state_type: "open", sort: "age", order: "asc", limit: 50 }),
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
  const topQueues = selectQueueShortcuts(flat);

  // Same "breached" definition the backend uses for `escalated`: any
  // escalation epoch already in the past. Oldest breach first.
  const escalatedTickets = (openForEscQ.data?.items ?? [])
    .map((ticket) => ({ ticket, epoch: nearestEscalation(ticket) }))
    .filter(
      (x): x is { ticket: TicketListItem; epoch: number } =>
        x.epoch != null && x.epoch <= nowSeconds,
    )
    .sort((a, b) => a.epoch - b.epoch);
  const escalationEpochById = new Map(escalatedTickets.map((x) => [x.ticket.id, x.epoch]));

  // Reminders due: overdue or due within the next 24h.
  const remindersDue = (pendingQ.data?.items ?? [])
    .filter((ticket) => ticket.until_time > 0 && ticket.until_time <= nowSeconds + 86400)
    .sort((a, b) => a.until_time - b.until_time)
    .slice(0, 6);

  const summary = summaryQ.data;
  // "Alle" is the union of the three real chip filters; escalated tickets
  // aren't counted here since they're not a chip of their own (they may
  // overlap with any of the three sets and get their own strip/group).
  const totalCount = (summary?.my_open ?? 0) + (summary?.my_new ?? 0) + (summary?.unowned_new ?? 0);
  const escalatedCount = summary?.escalated ?? 0;
  const oldestEscalatedEpoch = escalatedTickets[0]?.epoch ?? null;
  const unownedTop = (unownedQ.data?.items ?? []).slice(0, 8);

  return (
    <div className="mx-auto w-full max-w-6xl space-y-8 px-4 py-6" data-testid="dashboard">
      <div>
        <h1 className="font-display text-2xl font-semibold text-ink">
          {t(`dashboard.greeting.${greetingKey()}`, { name: user?.first_name || user?.login || "" })}
        </h1>
        <p className="mt-1 text-sm text-muted">
          {new Intl.DateTimeFormat(locale, {
            weekday: "long",
            day: "numeric",
            month: "long",
            year: "numeric",
          }).format(new Date())}
        </p>
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap gap-2" role="group" aria-label={t("dashboard.filterHeading")}>
          <FilterChip
            label={t("dashboard.chipAll")}
            count={totalCount}
            active={filter === "all"}
            onClick={() => setFilter("all")}
            testId="dashboard-chip-all"
          />
          <FilterChip
            label={t("dashboard.kpiMyOpen")}
            count={summary?.my_open ?? 0}
            active={filter === "mine-open"}
            onClick={() => selectChip("mine-open")}
            testId="dashboard-chip-mine-open"
          />
          <FilterChip
            label={t("dashboard.kpiMyNew")}
            count={summary?.my_new ?? 0}
            active={filter === "mine-new"}
            onClick={() => selectChip("mine-new")}
            testId="dashboard-chip-mine-new"
          />
          <FilterChip
            label={t("dashboard.kpiUnownedNew")}
            count={summary?.unowned_new ?? 0}
            active={filter === "takeover"}
            onClick={() => selectChip("takeover")}
            testId="dashboard-chip-takeover"
          />
        </div>

        <EscalationStrip
          count={escalatedCount}
          oldestEpoch={oldestEscalatedEpoch}
          active={filter === "escalated"}
          onShow={() => setFilter("escalated")}
        />
      </div>

      <div
        className="overflow-hidden rounded-lg border border-hairline bg-surface"
        data-testid="dashboard-ticket-list"
      >
        {filter === "all" && (
          <div className="divide-y divide-hairline">
            {escalatedCount > 0 && (
              <div>
                <GroupHeader slug="escalated" label={t("dashboard.kpiEscalated")} count={escalatedCount} />
                <DashboardTicketList
                  items={escalatedTickets.map((x) => x.ticket)}
                  loading={openForEscQ.isLoading}
                  emptyLabel={t("dashboard.escalatedEmpty")}
                  trailing={(ticket) => (
                    <span className="text-danger">
                      {formatDurationSince(escalationEpochById.get(ticket.id) ?? 0)}
                    </span>
                  )}
                  escalated
                />
              </div>
            )}
            <div>
              <GroupHeader slug="mine-new" label={t("dashboard.kpiMyNew")} count={summary?.my_new ?? 0} />
              <DashboardTicketList
                items={myNewQ.data?.items ?? []}
                loading={myNewQ.isLoading}
                emptyLabel={t("dashboard.myNewEmpty")}
                trailing={(ticket) => formatDateTime(ticket.create_time, locale)}
              />
            </div>
            <div>
              <GroupHeader slug="mine-open" label={t("dashboard.kpiMyOpen")} count={summary?.my_open ?? 0} />
              <DashboardTicketList
                items={lockedQ.data?.items ?? []}
                loading={lockedQ.isLoading}
                emptyLabel={t("dashboard.noMyTickets")}
                trailing={(ticket) => formatDateTime(ticket.change_time, locale)}
              />
            </div>
            <div>
              <GroupHeader
                slug="takeover"
                label={t("dashboard.groupTakeover")}
                count={summary?.unowned_new ?? 0}
                action={
                  (summary?.unowned_new ?? 0) > unownedTop.length ? (
                    <Link
                      to="/agent/queues"
                      search={{ state_type: "new" }}
                      className="text-[11px] font-medium normal-case tracking-normal text-accent hover:underline"
                    >
                      {t("dashboard.viewAll")}
                    </Link>
                  ) : undefined
                }
              />
              <DashboardTicketList
                items={unownedTop}
                loading={unownedQ.isLoading}
                emptyLabel={t("dashboard.toPickUpEmpty")}
                trailing={(ticket) => formatDateTime(ticket.create_time, locale)}
              />
            </div>
          </div>
        )}
        {filter === "mine-open" && (
          <DashboardTicketList
            items={lockedQ.data?.items ?? []}
            loading={lockedQ.isLoading}
            emptyLabel={t("dashboard.noMyTickets")}
            trailing={(ticket) => formatDateTime(ticket.change_time, locale)}
          />
        )}
        {filter === "mine-new" && (
          <DashboardTicketList
            items={myNewQ.data?.items ?? []}
            loading={myNewQ.isLoading}
            emptyLabel={t("dashboard.myNewEmpty")}
            trailing={(ticket) => formatDateTime(ticket.create_time, locale)}
          />
        )}
        {filter === "takeover" && (
          <DashboardTicketList
            items={unownedQ.data?.items ?? []}
            loading={unownedQ.isLoading}
            emptyLabel={t("dashboard.toPickUpEmpty")}
            trailing={(ticket) => formatDateTime(ticket.create_time, locale)}
          />
        )}
        {filter === "escalated" && (
          <DashboardTicketList
            items={escalatedTickets.map((x) => x.ticket)}
            loading={openForEscQ.isLoading}
            emptyLabel={t("dashboard.escalatedEmpty")}
            trailing={(ticket) => (
              <span className="text-danger">
                {formatDurationSince(escalationEpochById.get(ticket.id) ?? 0)}
              </span>
            )}
            escalated
          />
        )}
      </div>

      {/* Reminders due — orthogonal to the chips/groups above (due-date driven,
          not ownership/state driven), kept as its own section. */}
      <section id="widget-reminders" className="scroll-mt-4" aria-label={t("dashboard.remindersDue")}>
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">
          {t("dashboard.remindersDue")}
        </h2>
        <div className="overflow-hidden rounded-lg border border-hairline bg-surface">
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
        </div>
      </section>

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
        ) : topQueues.length === 0 ? (
          <p
            className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center text-sm text-muted"
            data-testid="dashboard-queues-empty"
          >
            {t("dashboard.queueShortcutsEmpty")}
          </p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {topQueues.map((q) => (
              <QueueShortcutCard key={q.id} queue={q} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
