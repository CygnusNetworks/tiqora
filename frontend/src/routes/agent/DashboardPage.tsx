import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import { flattenQueues } from "@/components/agent/QueueTree";
import { Spinner } from "@/components/ui/Spinner";
import { formatDateTime } from "@/lib/format";

export function DashboardPage() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const queuesQ = useQuery({
    queryKey: ["queues"],
    queryFn: () => api.listQueues(),
  });

  const lockedQ = useQuery({
    queryKey: ["tickets", "my-locked", user?.id],
    queryFn: () =>
      api.listTickets({
        owner_id: user?.id,
        state_type: "open",
        limit: 20,
        sort: "age",
        order: "desc",
      }),
    enabled: Boolean(user?.id),
  });

  const flat = flattenQueues(queuesQ.data ?? []);
  const topQueues = flat
    .slice()
    .sort((a, b) => (b.counts?.open ?? 0) - (a.counts?.open ?? 0))
    .slice(0, 8);

  return (
    <div className="mx-auto w-full max-w-6xl space-y-8 px-4 py-6" data-testid="dashboard">
      <div>
        <h1 className="font-display text-2xl font-semibold text-ink">
          {t("dashboard.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">
          {t("dashboard.welcome", {
            name: user?.first_name || user?.login || "",
          })}
        </p>
      </div>

      <section>
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">
          {t("dashboard.myTickets")}
        </h2>
        {lockedQ.isLoading ? (
          <Spinner />
        ) : (
          <div className="overflow-hidden rounded-lg border border-hairline bg-surface">
            {(lockedQ.data?.items ?? []).length === 0 ? (
              <p className="px-4 py-6 text-sm text-muted">{t("dashboard.noMyTickets")}</p>
            ) : (
              <ul className="divide-y divide-hairline">
                {(lockedQ.data?.items ?? []).map((ticket) => (
                  <li key={ticket.id}>
                    <Link
                      to="/agent/tickets/$ticketId"
                      params={{ ticketId: String(ticket.id) }}
                      className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm transition-colors duration-100 hover:bg-surface-subtle"
                    >
                      <span className="font-mono text-xs text-accent">{ticket.tn}</span>
                      <span className="min-w-0 flex-1 truncate">{ticket.title}</span>
                      <span className="shrink-0 font-mono text-xs tabular-nums text-muted">
                        {formatDateTime(ticket.change_time, locale)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

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
              <Link
                key={q.id}
                to="/agent/queues"
                search={{ queue_id: q.id, state_type: "open" }}
                className="rounded-lg border border-hairline bg-surface p-4 transition-colors duration-100 hover:border-accent/60"
                data-testid={`queue-shortcut-${q.id}`}
              >
                <p className="truncate text-xs uppercase tracking-wide text-muted" title={q.name}>
                  {q.name}
                </p>
                <p className="mt-2 font-mono text-2xl font-semibold tabular-nums text-ink">
                  {q.counts?.open ?? 0}
                </p>
                <p className="text-xs text-muted">{t("dashboard.openCount")}</p>
              </Link>
            ))}
            {topQueues.length === 0 && (
              <p className="text-sm text-muted col-span-full">{t("queue.empty")}</p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
