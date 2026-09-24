import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { api, type MutationRequest, type TicketListItem } from "@/lib/api";
import { flattenQueues } from "@/components/agent/QueueTree";
import {
  ESCALATION_SOON_SECONDS,
  TicketTable,
  type SortKey,
} from "@/components/agent/TicketTable";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Menu, MenuItem } from "@/components/ui/Menu";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { Spinner } from "@/components/ui/Spinner";
import { FlagIcon, LockIcon, MoreIcon, UserDashedIcon } from "@/components/ui/icons";
import { runConcurrent } from "@/lib/bulk";
import { cn } from "@/lib/cn";

/** Status segments. "todo" (new + open) is the default working view; the
 * rest filter to one state type each. */
const STATE_TABS = ["todo", "new", "open", "pending", "closed", "all"] as const;
type StateTab = (typeof STATE_TABS)[number];

/** Segment → backend `state_type` view. The backend's own "open" view means
 * new+open+pending (Znuny's viewable states), so the "Offen" segment asks for
 * the literal open type instead. */
const BACKEND_STATE_TYPE: Record<StateTab, string | undefined> = {
  todo: "todo",
  new: "new",
  open: "open_only",
  pending: "pending",
  closed: "closed",
  all: undefined,
};

/** Facet-count key per segment (see `TicketFacets.states`). */
const FACET_STATE_KEY: Record<StateTab, "todo" | "new" | "open_only" | "pending" | "closed" | "all"> =
  {
    todo: "todo",
    new: "new",
    open: "open_only",
    pending: "pending",
    closed: "closed",
    all: "all",
  };

const STATE_DOT: Partial<Record<StateTab, string>> = {
  new: "var(--color-state-new)",
  open: "var(--color-state-open)",
  pending: "var(--color-state-pending)",
  closed: "var(--color-state-closed)",
};

type FlagKey = "escalated" | "locked" | "unassigned";

export type QueuesSearch = {
  queue_id?: number;
  state_type?: StateTab;
  /** Exact-match filter on `customer_id`, set by clicking a ticket's customer cell. */
  customer_id?: string;
  /** Owner agent user id (e.g. "my tickets"). */
  owner_id?: number;
  /** Responsible agent user id. */
  responsible_id?: number;
  /** Service id (service-centric view). */
  service_id?: number;
  /** True = only locked tickets. */
  locked?: boolean;
  /** Watcher agent user id (e.g. "my watched"). */
  watcher_user_id?: number;
  /** True = only tickets with a breached escalation epoch. */
  escalated?: boolean;
  /** True = only tickets nobody owns yet. */
  unassigned?: boolean;
  /** Optional page title override key for preset views (i18n under views.*). */
  view?: "locked" | "mine" | "responsible" | "watched" | "escalated" | "service";
  offset?: number;
  limit?: number;
  sort?: SortKey;
  order?: "asc" | "desc";
  /** Admin-only: also list archived tickets (backend ignores it for non-admins). */
  include_archived?: boolean;
};

/** Hard cap on how many matching ticket ids "Alle M auswählen" will fetch —
 * protects against a filter matching tens of thousands of tickets. Backend
 * caps a single `listTickets` page at 200 (`ge=1, le=200` in
 * `backend/src/tiqora/api/v1/tickets.py`), so this is walked in 200-id pages. */
const SELECT_ALL_HARD_MAX = 2000;
const SELECT_ALL_PAGE_SIZE = 200;
/** Concurrent PATCH requests in flight for a bulk apply. */
const BULK_CONCURRENCY = 4;
/** How many overdue / due-soon tickets the pinned block shows before "show all". */
const PINNED_MAX = 5;

type BulkField = "state" | "priority" | "owner" | "queue" | "lock";

type FilterParams = {
  queue_id?: number;
  state_type?: string;
  customer_id?: string;
  owner_id?: number;
  responsible_id?: number;
  service_id?: number;
  locked?: boolean;
  watcher_user_id?: number;
  escalated?: boolean;
  unassigned?: boolean;
  include_archived?: boolean;
};

async function fetchMatchingTicketIds(
  params: FilterParams & { sort: SortKey; order: "asc" | "desc" },
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

/** Nearest SLA deadline that is set — the pinned block's sort key. */
function nearestDeadline(ticket: TicketListItem): number {
  const set = [
    ticket.escalation_time,
    ticket.escalation_response_time,
    ticket.escalation_update_time,
    ticket.escalation_solution_time,
  ].filter((e) => e > 0);
  return set.length ? Math.min(...set) : Number.POSITIVE_INFINITY;
}

export function QueuesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate({ from: "/agent/queues" });
  const search = useSearch({ from: "/agent/queues" }) as QueuesSearch;
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const isAdmin = user?.is_admin === true;

  const queueId = search.queue_id ?? null;
  const stateType = (search.state_type ?? "todo") as StateTab;
  const customerId = search.customer_id;
  const ownerId = search.owner_id;
  const responsibleId = search.responsible_id;
  const serviceId = search.service_id;
  const locked = search.locked;
  const watcherUserId = search.watcher_user_id;
  const escalated = search.escalated;
  const unassigned = search.unassigned;
  const view = search.view;
  const offset = search.offset ?? 0;
  const limit = search.limit ?? 50;
  const sort = (search.sort ?? "activity") as SortKey;
  const order = (search.order ?? "desc") as "asc" | "desc";
  const includeArchived = isAdmin && search.include_archived === true;

  /** Everything that decides which tickets match, except status and paging. */
  const scopeParams: FilterParams = {
    queue_id: queueId ?? undefined,
    customer_id: customerId,
    owner_id: ownerId,
    responsible_id: responsibleId,
    service_id: serviceId,
    watcher_user_id: watcherUserId,
    include_archived: includeArchived || undefined,
  };
  const filterParams: FilterParams = {
    ...scopeParams,
    state_type: BACKEND_STATE_TYPE[stateType],
    locked: locked || undefined,
    escalated: escalated || undefined,
    unassigned: unassigned || undefined,
  };
  const listParams = { ...filterParams, offset, limit, sort, order };

  const setSearch = (patch: Partial<QueuesSearch>) => {
    void navigate({
      search: (prev: QueuesSearch) => ({
        ...prev,
        ...patch,
      }),
      replace: true,
    });
  };

  // ── Selection ────────────────────────────────────────────────────────────
  // Checkboxes are always available on the rows; the action bar appears as
  // soon as something is selected. No separate "selection mode".
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [fullSelection, setFullSelection] = useState(false);
  const [fullIds, setFullIds] = useState<number[]>([]);
  const [fetchingAll, setFetchingAll] = useState(false);
  const lastToggled = useRef<number | null>(null);
  const [pendingAction, setPendingAction] = useState<{
    field: BulkField;
    value: number | string;
    label: string;
  } | null>(null);
  // Row-level quick edit (state/owner) — reference lists are only fetched
  // once the agent opens the first menu (either a row quick-edit trigger or
  // the bulk-action bar), not on page load.
  const [refDataRequested, setRefDataRequested] = useState(false);
  const [applying, setApplying] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [status, setStatus] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const clearSelection = () => {
    setSelected(new Set());
    setFullSelection(false);
    setFullIds([]);
    lastToggled.current = null;
  };

  // Selection is scoped to the current filter/page — reset it whenever
  // either changes so stale ids from a different view never leak into a
  // bulk action.
  useEffect(() => {
    clearSelection();
  }, [
    queueId,
    stateType,
    customerId,
    ownerId,
    responsibleId,
    serviceId,
    locked,
    watcherUserId,
    escalated,
    unassigned,
    offset,
    sort,
    order,
    includeArchived,
  ]);

  // Success feedback auto-dismisses; errors stay until the next action.
  useEffect(() => {
    if (status?.tone !== "success") return;
    const handle = window.setTimeout(() => setStatus(null), 6000);
    return () => window.clearTimeout(handle);
  }, [status]);

  const hasSelection = selected.size > 0 || fullSelection;
  useEffect(() => {
    if (!hasSelection) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        clearSelection();
        setStatus(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasSelection]);

  // Queue list is only needed for the header title now — the single queue
  // navigator lives in the app sidebar (AgentShell).
  const queuesQ = useQuery({
    queryKey: ["queues"],
    queryFn: () => api.listQueues(),
  });

  const ticketsQ = useQuery({
    queryKey: ["tickets", listParams],
    queryFn: () => api.listTickets(listParams),
  });

  // Counts for the status segments and flag chips — one request for all.
  const facetsQ = useQuery({
    queryKey: ["tickets", "facets", filterParams],
    queryFn: () => api.ticketFacets(filterParams),
  });

  // Pinned "needs attention" block: overdue and soon-due SLA deadlines stay
  // on top of the activity-sorted list, whatever their last activity was.
  // Only on the first page of the activity view — with the "Eskaliert" chip
  // on, or another sort, the list itself already answers that question.
  const showPinned =
    sort === "activity" && offset === 0 && !escalated && stateType !== "closed";
  const pinnedQ = useQuery({
    queryKey: ["tickets", "pinned", filterParams],
    queryFn: () =>
      api.listTickets({
        ...filterParams,
        escalating_within: ESCALATION_SOON_SECONDS,
        offset: 0,
        limit: 20,
        sort: "activity",
        order: "desc",
      }),
    enabled: showPinned,
  });
  const pinnedData = pinnedQ.data?.items;
  const pinnedItems = useMemo(
    () =>
      showPinned
        ? (pinnedData ?? [])
            // The backend already filters by `escalating_within`; re-check so
            // the block can never fill up with tickets that aren't due soon.
            .filter((t) => nearestDeadline(t) <= Date.now() / 1000 + ESCALATION_SOON_SECONDS)
            .sort((a, b) => nearestDeadline(a) - nearestDeadline(b))
            .slice(0, PINNED_MAX)
        : [],
    [showPinned, pinnedData],
  );

  const servicesQ = useQuery({
    queryKey: ["reference", "services"],
    queryFn: () => api.listReferenceServices(),
    enabled: serviceId != null || view === "service",
  });

  const wantsReferenceData = hasSelection || refDataRequested;
  const prioritiesQ = useQuery({
    queryKey: ["reference", "priorities"],
    queryFn: () => api.listReferencePriorities(),
    enabled: wantsReferenceData,
  });
  const statesQ = useQuery({
    queryKey: ["reference", "states"],
    queryFn: () => api.listReferenceStates(),
    enabled: wantsReferenceData,
  });
  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: () => api.listReferenceAgents(),
    enabled: wantsReferenceData,
  });

  const selectedQueueName = (() => {
    if (view) {
      const viewTitle = t(`views.${view}`, { defaultValue: "" });
      if (viewTitle) {
        if (view === "service" && serviceId != null) {
          const svc = (servicesQ.data ?? []).find((s) => s.id === serviceId);
          return svc ? `${viewTitle}: ${svc.name}` : viewTitle;
        }
        return viewTitle;
      }
    }
    if (queueId == null) return t("sidebar.inbox");
    const match = flattenQueues(queuesQ.data ?? []).find((q) => q.id === queueId);
    if (!match) return t("sidebar.inbox");
    return match.name.includes("::") ? (match.name.split("::").pop() ?? match.name) : match.name;
  })();

  const items = ticketsQ.data?.items ?? [];
  const total = ticketsQ.data?.total ?? 0;
  // Display order, as the table renders it: pinned block first, then the rest.
  const pinnedIds = new Set(pinnedItems.map((p) => p.id));
  const displayIds = [...pinnedItems.map((p) => p.id), ...items.filter((i) => !pinnedIds.has(i.id)).map((i) => i.id)];
  const effectiveSelected = fullSelection ? new Set(fullIds) : selected;
  const selectedIds = Array.from(effectiveSelected);
  const allPageSelected =
    displayIds.length > 0 && displayIds.every((id) => effectiveSelected.has(id));
  const somePageSelected =
    displayIds.some((id) => effectiveSelected.has(id)) && !allPageSelected;

  const toggleRow = (id: number, range = false) => {
    if (fullSelection) {
      setSelected(new Set(fullIds.filter((i) => i !== id)));
      setFullSelection(false);
      lastToggled.current = id;
      return;
    }
    const anchor = lastToggled.current;
    lastToggled.current = id;
    setSelected((prev) => {
      const next = new Set(prev);
      if (range && anchor != null && anchor !== id) {
        const a = displayIds.indexOf(anchor);
        const b = displayIds.indexOf(id);
        if (a >= 0 && b >= 0) {
          const [from, to] = a < b ? [a, b] : [b, a];
          for (const rid of displayIds.slice(from, to + 1)) next.add(rid);
          return next;
        }
      }
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
        for (const id of displayIds) next.delete(id);
      } else {
        for (const id of displayIds) next.add(id);
      }
      return next;
    });
  };

  const selectAllMatches = async () => {
    setFetchingAll(true);
    try {
      const ids = await fetchMatchingTicketIds({ ...filterParams, sort, order }, total);
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
    const body: MutationRequest =
      pendingAction.field === "state"
        ? { state_id: Number(pendingAction.value) }
        : pendingAction.field === "priority"
          ? { priority_id: Number(pendingAction.value) }
          : pendingAction.field === "owner"
            ? { owner_id: Number(pendingAction.value) }
            : pendingAction.field === "queue"
              ? { queue_id: Number(pendingAction.value) }
              : { lock: String(pendingAction.value) };

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
    // Bulk apply calls api.patchTicket directly (not usePatchTicket) to run
    // requests concurrently — mirror its invalidation here so the sidebar
    // queue badges refresh the same way a single-ticket patch does.
    void queryClient.invalidateQueries({ queryKey: ["tickets"] });
    void queryClient.invalidateQueries({ queryKey: ["queues"] });
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
  const bulkQueueItems: SelectMenuItem<number>[] = flattenQueues(queuesQ.data ?? [])
    .filter((q) => q.valid)
    .map((q) => ({ value: q.id, label: q.name }));
  const lockItems: SelectMenuItem<string>[] = [
    { value: "lock", label: t("ticket.toolbar.lock", { defaultValue: "Lock" }) },
    { value: "unlock", label: t("ticket.toolbar.unlock", { defaultValue: "Unlock" }) },
  ];

  // Row quick edit: one ticket at a time, same invalidation as
  // usePatchTicket/the bulk apply above so the sidebar badges stay in sync.
  const rowPatch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: MutationRequest }) => api.patchTicket(id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tickets"] });
      void queryClient.invalidateQueries({ queryKey: ["queues"] });
    },
  });

  const noSelection = selectedIds.length === 0;
  const facets = facetsQ.data;
  const flagValue: Record<FlagKey, boolean> = {
    escalated: escalated === true,
    locked: locked === true,
    unassigned: unassigned === true,
  };
  const anyFlag = flagValue.escalated || flagValue.locked || flagValue.unassigned;
  const flagChips: { key: FlagKey; icon: ReactNode; label: string; tone?: "danger" }[] = [
    { key: "escalated", icon: <FlagIcon />, label: t("queue.flags.escalated"), tone: "danger" },
    { key: "locked", icon: <LockIcon />, label: t("queue.flags.locked") },
    { key: "unassigned", icon: <UserDashedIcon />, label: t("queue.flags.unassigned") },
  ];

  const exportCsv = () => {
    window.location.href = api.exportTicketsCsvUrl({ ...filterParams, sort, order });
  };

  // A queue view already names its queue in the title; only the cross-queue
  // views say so explicitly.
  const metaLine = [
    queueId == null ? t("queue.allQueues") : null,
    sort === "activity" ? t("queue.byActivity") : null,
  ]
    .filter(Boolean)
    .join(" · ");

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
              {t("queue.countBadge", { count: total })}
            </span>
            {customerId && (
              <span
                className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent-dim px-2.5 py-0.5 font-mono text-[11px] text-accent"
                data-testid="queue-customer-filter-chip"
              >
                {t("queue.customerFilter", { customerId })}
                <button
                  type="button"
                  className="ml-0.5 leading-none hover:text-ink"
                  aria-label={t("queue.clearCustomerFilter")}
                  data-testid="queue-customer-filter-clear"
                  onClick={() => setSearch({ customer_id: undefined, offset: 0 })}
                >
                  ×
                </button>
              </span>
            )}
          </div>
          {metaLine && (
            <p className="mt-0.5 text-[12.5px] text-muted" data-testid="queue-meta-line">
              {metaLine}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div
            role="tablist"
            aria-label={t("queue.stateFilter")}
            className="inline-flex flex-wrap items-center gap-0.5 rounded-lg border border-hairline bg-surface p-[3px]"
            data-testid="queue-state-tabs"
          >
            {STATE_TABS.map((id) => {
              const active = id === stateType;
              const count = facets?.states?.[FACET_STATE_KEY[id]];
              return (
                <span key={id} className="contents">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={active}
                    data-testid={`queue-state-tab-${id}`}
                    onClick={() => setSearch({ state_type: id, offset: 0 })}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12.5px] transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
                      active
                        ? "bg-surface-subtle font-medium text-ink"
                        : "text-muted hover:text-ink",
                    )}
                  >
                    {STATE_DOT[id] && (
                      <span
                        aria-hidden
                        className="h-1.5 w-1.5 rounded-full"
                        style={{ background: STATE_DOT[id] }}
                      />
                    )}
                    {t(`queue.state.${id}`)}
                    {count != null && (
                      <span
                        className={cn(
                          "font-mono text-[10.5px] tabular-nums",
                          active ? "text-ink/80" : "text-muted",
                        )}
                      >
                        {count}
                      </span>
                    )}
                  </button>
                  {id === "todo" && <span aria-hidden className="mx-0.5 h-4 w-px bg-hairline" />}
                </span>
              );
            })}
          </div>

          <Menu
            align="right"
            panelTestId="queue-more-menu"
            trigger={({ ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                {...toggleProps}
                aria-label={t("queue.moreActions")}
                data-testid="queue-more-actions"
                className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-hairline bg-surface text-muted transition-colors duration-100 hover:border-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              >
                <MoreIcon className="h-4 w-4" />
              </button>
            )}
          >
            {isAdmin && (
              <MenuItem
                selected={includeArchived}
                keepOpen
                testId="queue-show-archived"
                onSelect={() =>
                  setSearch({ include_archived: includeArchived ? undefined : true, offset: 0 })
                }
              >
                {t("queue.showArchived")}
              </MenuItem>
            )}
            <MenuItem testId="queue-export-csv" onSelect={exportCsv}>
              {t("queue.exportCsv")}
            </MenuItem>
          </Menu>
        </div>

        <div
          className="flex flex-wrap items-center gap-1.5"
          role="group"
          aria-label={t("queue.flags.label")}
          data-testid="queue-flag-chips"
        >
          <span className="mr-0.5 text-[12px] text-muted">{t("queue.flags.only")}</span>
          {flagChips.map((chip) => {
            const active = flagValue[chip.key];
            const count = facets?.flags?.[chip.key];
            const empty = count === 0 && !active;
            return (
              <button
                key={chip.key}
                type="button"
                aria-pressed={active}
                disabled={empty}
                data-testid={`queue-flag-${chip.key}`}
                onClick={() => setSearch({ [chip.key]: active ? undefined : true, offset: 0 })}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[12px] transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent [&_svg]:h-3.5 [&_svg]:w-3.5",
                  active
                    ? chip.tone === "danger"
                      ? "border-danger/50 bg-danger/10 text-danger"
                      : "border-accent/50 bg-accent-dim text-accent"
                    : "border-hairline bg-surface text-muted hover:border-muted hover:text-ink",
                  empty && "cursor-default opacity-45 hover:border-hairline hover:text-muted",
                )}
              >
                {chip.icon}
                {chip.label}
                {count != null && (
                  <span
                    className={cn(
                      "font-mono text-[10.5px] tabular-nums",
                      chip.tone === "danger" && count > 0 && "font-semibold text-danger",
                    )}
                  >
                    {count}
                  </span>
                )}
              </button>
            );
          })}
          {anyFlag && (
            <button
              type="button"
              data-testid="queue-flag-reset"
              onClick={() =>
                setSearch({ escalated: undefined, locked: undefined, unassigned: undefined, offset: 0 })
              }
              className="px-1.5 text-[12px] text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
            >
              {t("queue.flags.reset")}
            </button>
          )}
        </div>

        {(hasSelection || status) && (
          <div
            className="flex flex-wrap items-center gap-3 rounded-lg border border-accent/40 bg-accent-dim px-3 py-2 text-[12.5px] text-ink"
            data-testid="queue-select-banner"
          >
            {hasSelection && (
              <>
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
                  {!fullSelection && total > displayIds.length && (
                    <>
                      <span aria-hidden>·</span>
                      <button
                        type="button"
                        className="font-medium text-accent underline-offset-2 hover:underline disabled:opacity-50"
                        data-testid="queue-select-all-matches"
                        disabled={fetchingAll || total === 0}
                        onClick={() => void selectAllMatches()}
                      >
                        {fetchingAll
                          ? t("queue.bulk.selectAllMatchesLoading")
                          : t("queue.bulk.selectAllMatches", {
                              count: total,
                              queue: selectedQueueName,
                            })}
                      </button>
                    </>
                  )}
                </span>

                <div className="ml-auto flex flex-wrap items-center gap-2">
                  <BulkMenu
                    items={stateItems}
                    disabled={noSelection}
                    testId="queue-bulk-state"
                    label={t("queue.bulk.fieldState")}
                    placeholder={t("ticket.dialog.selectPlaceholder")}
                    onSelect={(value, label) => setPendingAction({ field: "state", value, label })}
                  />
                  <BulkMenu
                    items={priorityItems}
                    disabled={noSelection}
                    testId="queue-bulk-priority"
                    label={t("queue.bulk.fieldPriority")}
                    placeholder={t("ticket.dialog.selectPlaceholder")}
                    onSelect={(value, label) =>
                      setPendingAction({ field: "priority", value, label })
                    }
                  />
                  <BulkMenu
                    items={agentItems}
                    searchThreshold={8}
                    disabled={noSelection}
                    testId="queue-bulk-owner"
                    label={t("queue.bulk.fieldOwner")}
                    placeholder={t("ticket.dialog.selectPlaceholder")}
                    onSelect={(value, label) => setPendingAction({ field: "owner", value, label })}
                  />
                  <BulkMenu
                    items={bulkQueueItems}
                    searchThreshold={8}
                    disabled={noSelection}
                    testId="queue-bulk-queue"
                    label={t("queue.bulk.fieldQueue", { defaultValue: "Queue" })}
                    placeholder={t("ticket.dialog.selectPlaceholder")}
                    onSelect={(value, label) => setPendingAction({ field: "queue", value, label })}
                  />
                  <BulkMenu
                    items={lockItems}
                    disabled={noSelection}
                    testId="queue-bulk-lock"
                    label={t("queue.bulk.fieldLock", { defaultValue: "Lock" })}
                    placeholder={t("ticket.dialog.selectPlaceholder")}
                    onSelect={(value, label) => setPendingAction({ field: "lock", value, label })}
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    data-testid="queue-select-clear"
                    aria-label={t("queue.bulk.clearSelection")}
                    onClick={() => {
                      clearSelection();
                      setStatus(null);
                    }}
                  >
                    ✕
                  </Button>
                </div>
              </>
            )}

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
          groupByDay={sort === "activity" && order === "desc"}
          hideQueue={queueId != null}
          navContext={{ label: selectedQueueName, to: "/agent/queues", search }}
          pinned={
            pinnedItems.length
              ? {
                  items: pinnedItems,
                  total: pinnedQ.data?.total ?? pinnedItems.length,
                  onShowAll: () => setSearch({ escalated: true, offset: 0 }),
                }
              : undefined
          }
          onSortChange={(s, o) => setSearch({ sort: s, order: o, offset: 0 })}
          onPageChange={(off) => setSearch({ offset: off })}
          onCustomerClick={(id) => setSearch({ customer_id: id, offset: 0 })}
          selection={{
            selected: effectiveSelected,
            onToggleRow: toggleRow,
            onToggleAllPage: toggleAllPage,
            allPageSelected,
            somePageSelected,
          }}
          quickEdit={{
            stateItems,
            priorityItems,
            agentItems,
            agentsLoading: agentsQ.isLoading,
            onRequestOptions: () => setRefDataRequested(true),
            onPatch: (id, body) => rowPatch.mutate({ id, body }),
          }}
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

/** One field dropdown in the bulk-action bar (state, priority, owner, …). */
function BulkMenu<T extends string | number>({
  items,
  disabled,
  testId,
  label,
  placeholder,
  searchThreshold,
  onSelect,
}: {
  items: SelectMenuItem<T>[];
  disabled: boolean;
  testId: string;
  label: string;
  placeholder: string;
  searchThreshold?: number;
  onSelect: (value: T, label: string) => void;
}) {
  return (
    <SelectMenu
      items={items}
      searchThreshold={searchThreshold}
      onSelect={(value) => onSelect(value, items.find((i) => i.value === value)?.label ?? "")}
      placeholder={placeholder}
      panelTestId={`${testId}-menu`}
      trigger={({ ref, toggleProps }) => (
        <button
          ref={ref}
          type="button"
          disabled={disabled}
          data-testid={testId}
          {...toggleProps}
          className="inline-flex items-center gap-1 rounded-md border border-hairline bg-surface px-2.5 py-1 text-xs font-medium text-ink transition-colors duration-100 hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-50"
        >
          {label} ⌄
        </button>
      )}
    />
  );
}

function capitalize(field: BulkField): string {
  return field.charAt(0).toUpperCase() + field.slice(1);
}
