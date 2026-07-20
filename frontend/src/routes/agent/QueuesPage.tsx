import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { flattenQueues } from "@/components/agent/QueueTree";
import {
  TicketTable,
  type SortKey,
} from "@/components/agent/TicketTable";
import { Tabs } from "@/components/ui/Tabs";
import { Button } from "@/components/ui/Button";

const STATE_TABS = ["new", "open", "pending", "closed", "all"] as const;
type StateTab = (typeof STATE_TABS)[number];

export type QueuesSearch = {
  queue_id?: number;
  state_type?: StateTab;
  offset?: number;
  limit?: number;
  sort?: SortKey;
  order?: "asc" | "desc";
};

export function QueuesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate({ from: "/agent/queues" });
  const search = useSearch({ from: "/agent/queues" }) as QueuesSearch;

  const queueId = search.queue_id ?? null;
  const stateType = (search.state_type ?? "open") as StateTab;
  const offset = search.offset ?? 0;
  const limit = search.limit ?? 50;
  const sort = (search.sort ?? "age") as SortKey;
  const order = (search.order ?? "desc") as "asc" | "desc";

  const setSearch = (patch: Partial<QueuesSearch>) => {
    void navigate({
      search: (prev: QueuesSearch) => ({
        ...prev,
        ...patch,
      }),
      replace: true,
    });
  };

  // Queue list is only needed for the header title now — the single queue
  // navigator lives in the app sidebar (AgentShell), so QueuesPage no longer
  // renders its own queue tree and the ticket table takes the full width.
  const queuesQ = useQuery({
    queryKey: ["queues"],
    queryFn: () => api.listQueues(),
  });

  const ticketsQ = useQuery({
    queryKey: [
      "tickets",
      { queueId, stateType, offset, limit, sort, order },
    ],
    queryFn: () =>
      api.listTickets({
        queue_id: queueId ?? undefined,
        state_type: stateType === "all" ? undefined : stateType,
        offset,
        limit,
        sort,
        order,
      }),
  });

  const selectedQueueName =
    queueId == null
      ? t("sidebar.inbox")
      : (() => {
          const match = flattenQueues(queuesQ.data ?? []).find((q) => q.id === queueId);
          if (!match) return t("sidebar.inbox");
          return match.name.includes("::") ? (match.name.split("::").pop() ?? match.name) : match.name;
        })();

  return (
    <div className="relative flex min-h-0 flex-1" data-testid="queues-page">
      <div className="min-w-0 flex-1 space-y-3 p-3">
        <div>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="font-display text-xl font-bold tracking-tight text-ink">
              {selectedQueueName}
            </h1>
            <span
              className="rounded-full bg-accent-dim px-2.5 py-0.5 font-mono text-[11px] tabular-nums text-accent"
              data-testid="queue-open-badge"
            >
              {t("queue.openBadge", { count: ticketsQ.data?.total ?? 0 })}
            </span>
          </div>
          <p className="mt-0.5 text-[12.5px] text-muted">{t("queue.metaLine")}</p>
        </div>
        <div className="flex items-center gap-2">
          <Tabs
            value={stateType}
            onChange={(id) =>
              setSearch({ state_type: id as StateTab, offset: 0 })
            }
            items={STATE_TABS.map((id) => ({
              id,
              label: t(`queue.state.${id}`),
            }))}
            className="flex-1"
          />
          <Button
            variant="secondary"
            size="sm"
            data-testid="queue-export-csv"
            onClick={() => {
              window.location.href = api.exportTicketsCsvUrl({
                queue_id: queueId ?? undefined,
                state_type: stateType === "all" ? undefined : stateType,
                sort,
                order,
              });
            }}
          >
            {t("queue.exportCsv")}
          </Button>
        </div>
        <TicketTable
          items={ticketsQ.data?.items ?? []}
          total={ticketsQ.data?.total ?? 0}
          offset={offset}
          limit={limit}
          sort={sort}
          order={order}
          isLoading={ticketsQ.isLoading}
          onSortChange={(s, o) => setSearch({ sort: s, order: o, offset: 0 })}
          onPageChange={(off) => setSearch({ offset: off })}
        />
      </div>
    </div>
  );
}
