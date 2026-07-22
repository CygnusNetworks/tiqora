import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
import { Dialog } from "@/components/ui/Dialog";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { Spinner } from "@/components/ui/Spinner";
import { runConcurrent } from "@/lib/bulk";
import { cn } from "@/lib/cn";

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

/** Hard cap on how many matching ticket ids "Alle M auswählen" will fetch —
 * protects against a filter matching tens of thousands of tickets. Backend
 * caps a single `listTickets` page at 200 (`ge=1, le=200` in
 * `backend/src/tiqora/api/v1/tickets.py`), so this is walked in 200-id pages. */
const SELECT_ALL_HARD_MAX = 2000;
const SELECT_ALL_PAGE_SIZE = 200;
/** Concurrent PATCH requests in flight for a bulk apply. */
const BULK_CONCURRENCY = 4;

type BulkField = "state" | "priority" | "owner";

async function fetchMatchingTicketIds(
  params: { queue_id?: number; state_type?: string; sort: SortKey; order: "asc" | "desc" },
  total: number,
): Promise<number[]> {
  const cap = Math.min(total, SELECT_ALL_HARD_MAX);
  const ids: number[] = [];
  let offset = 0;
  while (ids.length < cap) {
    const limit = Math.min(SELECT_ALL_PAGE_SIZE, cap - ids.length);
    const page = await api.listTickets({ ...params, offset, limit });
    if (page.items.length === 0) break;
    ids.push(...page.items.map((item) => item.id));
    offset += page.items.length;
  }
  return ids;
}

export function QueuesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate({ from: "/agent/queues" });
  const search = useSearch({ from: "/agent/queues" }) as QueuesSearch;
  const queryClient = useQueryClient();

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

  // ── Selection mode ("Auswahlmodus", Variante B) ──────────────────────────
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [fullSelection, setFullSelection] = useState(false);
  const [fullIds, setFullIds] = useState<number[]>([]);
  const [fetchingAll, setFetchingAll] = useState(false);
  const [pendingAction, setPendingAction] = useState<{
    field: BulkField;
    value: number;
    label: string;
  } | null>(null);
  const [applying, setApplying] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [status, setStatus] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const clearSelection = () => {
    setSelected(new Set());
    setFullSelection(false);
    setFullIds([]);
  };

  const exitSelectMode = () => {
    setSelectMode(false);
    clearSelection();
    setStatus(null);
  };

  // Selection is scoped to the current filter/page — reset it whenever
  // either changes so stale ids from a different view never leak into a
  // bulk action.
  useEffect(() => {
    clearSelection();
  }, [queueId, stateType, offset, sort, order]);

  // Success feedback auto-dismisses; errors stay until the next action.
  useEffect(() => {
    if (status?.tone !== "success") return;
    const handle = window.setTimeout(() => setStatus(null), 6000);
    return () => window.clearTimeout(handle);
  }, [status]);

  useEffect(() => {
    if (!selectMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") exitSelectMode();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectMode]);

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

  const prioritiesQ = useQuery({
    queryKey: ["reference", "priorities"],
    queryFn: () => api.listReferencePriorities(),
    enabled: selectMode,
  });
  const statesQ = useQuery({
    queryKey: ["reference", "states"],
    queryFn: () => api.listReferenceStates(),
    enabled: selectMode,
  });
  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: () => api.listReferenceAgents(),
    enabled: selectMode,
  });

  const selectedQueueName =
    queueId == null
      ? t("sidebar.inbox")
      : (() => {
          const match = flattenQueues(queuesQ.data ?? []).find((q) => q.id === queueId);
          if (!match) return t("sidebar.inbox");
          return match.name.includes("::") ? (match.name.split("::").pop() ?? match.name) : match.name;
        })();

  const items = ticketsQ.data?.items ?? [];
  const total = ticketsQ.data?.total ?? 0;
  const pageIds = items.map((item) => item.id);
  const effectiveSelected = fullSelection ? new Set(fullIds) : selected;
  const selectedIds = Array.from(effectiveSelected);
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => effectiveSelected.has(id));
  const somePageSelected = pageIds.some((id) => effectiveSelected.has(id)) && !allPageSelected;

  const toggleRow = (id: number) => {
    if (fullSelection) {
      setSelected(new Set(fullIds.filter((i) => i !== id)));
      setFullSelection(false);
      return;
    }
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllPage = () => {
    if (fullSelection) {
      clearSelection();
      return;
    }
    setSelected((prev) => {
      const next = new Set(prev);
      if (allPageSelected) {
        for (const id of pageIds) next.delete(id);
      } else {
        for (const id of pageIds) next.add(id);
      }
      return next;
    });
  };

  const selectAllMatches = async () => {
    setFetchingAll(true);
    try {
      const ids = await fetchMatchingTicketIds(
        {
          queue_id: queueId ?? undefined,
          state_type: stateType === "all" ? undefined : stateType,
          sort,
          order,
        },
        total,
      );
      setFullIds(ids);
      setFullSelection(true);
      setSelected(new Set());
    } finally {
      setFetchingAll(false);
    }
  };

  const applyBulkAction = async () => {
    if (!pendingAction) return;
    const ids = selectedIds;
    const body =
      pendingAction.field === "state"
        ? { state_id: pendingAction.value }
        : pendingAction.field === "priority"
          ? { priority_id: pendingAction.value }
          : { owner_id: pendingAction.value };

    setApplying(true);
    setStatus(null);
    setProgress({ done: 0, total: ids.length });
    const { succeeded, failed } = await runConcurrent(
      ids,
      (id) => api.patchTicket(id, body),
      BULK_CONCURRENCY,
      (done, doneTotal) => setProgress({ done, total: doneTotal }),
    );
    setApplying(false);
    setProgress(null);
    setPendingAction(null);

    if (failed.length === 0) {
      setStatus({ tone: "success", text: t("queue.bulk.updated", { count: succeeded.length }) });
      clearSelection();
    } else {
      setStatus({
        tone: "error",
        text: t("queue.bulk.partialFail", {
          done: succeeded.length,
          failed: failed.length,
          ids: failed.join(", "),
        }),
      });
      setSelected(new Set(failed));
      setFullSelection(false);
      setFullIds([]);
    }
    void queryClient.invalidateQueries({ queryKey: ["tickets"] });
  };

  const stateItems: SelectMenuItem<number>[] = (statesQ.data ?? []).map((s) => ({
    value: s.id,
    label: s.name,
  }));
  const priorityItems: SelectMenuItem<number>[] = (prioritiesQ.data ?? []).map((p) => ({
    value: p.id,
    label: p.name,
  }));
  const agentItems: SelectMenuItem<number>[] = (agentsQ.data ?? []).map((a) => ({
    value: a.id,
    label: a.full_name,
    hint: a.login,
  }));

  const noSelection = selectedIds.length === 0;

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
          {!selectMode && (
            <Button
              variant="secondary"
              size="sm"
              data-testid="queue-select-mode"
              onClick={() => setSelectMode(true)}
            >
              {t("queue.bulk.select")}
            </Button>
          )}
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

        {selectMode && (
          <div
            className="flex flex-wrap items-center gap-3 rounded-lg border border-accent/40 bg-accent-dim px-3 py-2 text-[12.5px] text-ink"
            data-testid="queue-select-banner"
          >
            <span className="flex flex-wrap items-center gap-1.5">
              {fullSelection ? (
                <span data-testid="queue-select-all-status">
                  {total > SELECT_ALL_HARD_MAX
                    ? t("queue.bulk.allSelectedCapped", {
                        count: fullIds.length,
                        cap: SELECT_ALL_HARD_MAX,
                      })
                    : t("queue.bulk.allSelected", { count: fullIds.length })}
                </span>
              ) : (
                <span data-testid="queue-selected-count">
                  {t("queue.bulk.selectedOnPage", { count: selected.size })}
                </span>
              )}
              <span aria-hidden>·</span>
              {fullSelection ? (
                <button
                  type="button"
                  className="font-medium text-accent underline-offset-2 hover:underline"
                  onClick={clearSelection}
                >
                  {t("queue.bulk.clearSelection")}
                </button>
              ) : (
                <button
                  type="button"
                  className="font-medium text-accent underline-offset-2 hover:underline disabled:opacity-50"
                  data-testid="queue-select-all-matches"
                  disabled={fetchingAll || total === 0}
                  onClick={() => void selectAllMatches()}
                >
                  {fetchingAll
                    ? t("queue.bulk.selectAllMatchesLoading")
                    : t("queue.bulk.selectAllMatches", { count: total, queue: selectedQueueName })}
                </button>
              )}
            </span>

            <div className="ml-auto flex flex-wrap items-center gap-2">
              <SelectMenu
                items={stateItems}
                onSelect={(value) => {
                  const label = stateItems.find((i) => i.value === value)?.label ?? "";
                  setPendingAction({ field: "state", value, label });
                }}
                placeholder={t("ticket.dialog.selectPlaceholder")}
                panelTestId="queue-bulk-state-menu"
                trigger={({ ref, toggleProps }) => (
                  <button
                    ref={ref}
                    type="button"
                    disabled={noSelection}
                    data-testid="queue-bulk-state"
                    {...toggleProps}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-md border border-hairline bg-surface px-2.5 py-1 text-xs font-medium text-ink transition-colors duration-100 hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-50",
                    )}
                  >
                    {t("queue.bulk.fieldState")} ⌄
                  </button>
                )}
              />
              <SelectMenu
                items={priorityItems}
                onSelect={(value) => {
                  const label = priorityItems.find((i) => i.value === value)?.label ?? "";
                  setPendingAction({ field: "priority", value, label });
                }}
                placeholder={t("ticket.dialog.selectPlaceholder")}
                panelTestId="queue-bulk-priority-menu"
                trigger={({ ref, toggleProps }) => (
                  <button
                    ref={ref}
                    type="button"
                    disabled={noSelection}
                    data-testid="queue-bulk-priority"
                    {...toggleProps}
                    className="inline-flex items-center gap-1 rounded-md border border-hairline bg-surface px-2.5 py-1 text-xs font-medium text-ink transition-colors duration-100 hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {t("queue.bulk.fieldPriority")} ⌄
                  </button>
                )}
              />
              <SelectMenu
                items={agentItems}
                searchThreshold={8}
                onSelect={(value) => {
                  const label = agentItems.find((i) => i.value === value)?.label ?? "";
                  setPendingAction({ field: "owner", value, label });
                }}
                placeholder={t("ticket.dialog.selectPlaceholder")}
                panelTestId="queue-bulk-owner-menu"
                trigger={({ ref, toggleProps }) => (
                  <button
                    ref={ref}
                    type="button"
                    disabled={noSelection}
                    data-testid="queue-bulk-owner"
                    {...toggleProps}
                    className="inline-flex items-center gap-1 rounded-md border border-hairline bg-surface px-2.5 py-1 text-xs font-medium text-ink transition-colors duration-100 hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {t("queue.bulk.fieldOwner")} ⌄
                  </button>
                )}
              />
              <Button
                variant="ghost"
                size="sm"
                data-testid="queue-select-done"
                onClick={exitSelectMode}
              >
                {t("queue.bulk.done")}
              </Button>
            </div>

            {applying && progress && (
              <span
                className="w-full font-mono text-[11px] tabular-nums text-accent"
                data-testid="queue-bulk-progress"
              >
                {t("queue.bulk.progress", { done: progress.done, total: progress.total })}
              </span>
            )}
            {status && (
              <span
                className={cn(
                  "w-full text-xs font-medium",
                  status.tone === "success" ? "text-green" : "text-danger",
                )}
                data-testid="queue-bulk-status"
              >
                {status.text}
              </span>
            )}
          </div>
        )}

        <TicketTable
          items={items}
          total={total}
          offset={offset}
          limit={limit}
          sort={sort}
          order={order}
          isLoading={ticketsQ.isLoading}
          onSortChange={(s, o) => setSearch({ sort: s, order: o, offset: 0 })}
          onPageChange={(off) => setSearch({ offset: off })}
          selection={
            selectMode
              ? {
                  selected: effectiveSelected,
                  onToggleRow: toggleRow,
                  onToggleAllPage: toggleAllPage,
                  allPageSelected,
                  somePageSelected,
                }
              : undefined
          }
        />
      </div>

      {pendingAction && (
        <Dialog
          open
          onClose={() => (applying ? undefined : setPendingAction(null))}
          title={t("queue.bulk.confirmTitle", {
            field: t(`queue.bulk.field${capitalize(pendingAction.field)}`),
          })}
        >
          <p className="mb-4">
            {t("queue.bulk.confirmText", {
              field: t(`queue.bulk.field${capitalize(pendingAction.field)}`),
              value: pendingAction.label,
              count: selectedIds.length,
            })}
          </p>
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={applying}
              onClick={() => setPendingAction(null)}
            >
              {t("queue.bulk.confirmCancel")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={applying}
              data-testid="queue-bulk-confirm"
              onClick={() => void applyBulkAction()}
            >
              {applying && <Spinner className="mr-1 h-3 w-3" />}
              {t("queue.bulk.confirmApply")}
            </Button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

function capitalize(field: BulkField): string {
  return field.charAt(0).toUpperCase() + field.slice(1);
}
