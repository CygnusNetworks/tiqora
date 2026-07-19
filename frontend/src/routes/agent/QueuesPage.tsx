import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { QueueTree } from "@/components/agent/QueueTree";
import {
  TicketTable,
  type SortKey,
} from "@/components/agent/TicketTable";
import { Tabs } from "@/components/ui/Tabs";
import { Spinner } from "@/components/ui/Spinner";
import { Button } from "@/components/ui/Button";

const STATE_TABS = ["open", "pending", "closed", "all"] as const;
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
  const [drawerOpen, setDrawerOpen] = useState(false);

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

  const sidebarBody = queuesQ.isLoading ? (
    <div className="flex justify-center py-6">
      <Spinner />
    </div>
  ) : (
    <QueueTree
      queues={queuesQ.data ?? []}
      selectedId={queueId}
      onSelect={(id) => {
        setSearch({ queue_id: id ?? undefined, offset: 0 });
        setDrawerOpen(false);
      }}
    />
  );

  return (
    <div className="relative flex min-h-0 flex-1" data-testid="queues-page">
      {/* Desktop / tablet sidebar */}
      <aside className="hidden w-56 shrink-0 overflow-y-auto border-r border-hairline bg-surface p-2 md:block lg:w-64">
        <h2 className="mb-2 px-2 text-xs font-semibold uppercase tracking-wide text-muted">
          {t("queue.sidebar")}
        </h2>
        {sidebarBody}
      </aside>

      {/* Mobile drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-30 md:hidden">
          <button
            type="button"
            aria-label={t("common.back")}
            className="absolute inset-0 bg-black/40"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 w-64 overflow-y-auto border-r border-hairline bg-surface p-2 shadow-xl">
            <div className="mb-2 flex items-center justify-between px-2">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
                {t("queue.sidebar")}
              </h2>
              <Button variant="ghost" size="sm" onClick={() => setDrawerOpen(false)}>
                ✕
              </Button>
            </div>
            {sidebarBody}
          </div>
        </div>
      )}

      <div className="min-w-0 flex-1 space-y-3 p-3">
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            className="md:hidden"
            onClick={() => setDrawerOpen(true)}
            data-testid="queue-drawer-toggle"
          >
            {t("queue.sidebar")}
          </Button>
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
