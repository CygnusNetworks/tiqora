import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { api, type MutationRequest, type TicketListChannel, type TicketListItem } from "@/lib/api";
import { flattenQueues } from "@/components/agent/QueueTree";
import { BulkEditPanel } from "@/components/agent/BulkEditPanel";
import {
  clearDraftField,
  draftReady,
  draftToMutation,
  isClosedStateId,
  isPendingStateId,
  pendingQuickPicks,
  type BulkDraft,
  type BulkField,
} from "@/lib/bulkDraft";
import {
  ESCALATION_SOON_SECONDS,
  TicketTable,
  type SortKey,
} from "@/components/agent/TicketTable";
import { Button } from "@/components/ui/Button";
import { Menu, MenuItem } from "@/components/ui/Menu";
import type { SelectMenuItem } from "@/components/ui/SelectMenu";
import {
  CheckIcon,
  ClockIcon,
  FlagIcon,
  FolderIcon,
  LockIcon,
  MoreIcon,
  PencilIcon,
  UserDashedIcon,
  UserIcon,
} from "@/components/ui/icons";
import { TICKET_CHANNEL_KEYS, TICKET_CHANNELS } from "@/lib/ticketChannel";
import { runConcurrent } from "@/lib/bulk";
import { cn } from "@/lib/cn";
import { priorityLabel } from "@/lib/priority";
import { stateLabel } from "@/lib/status";

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
  /** Only these channels (any of them); unset = all. */
  channel?: TicketListChannel[];
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
  channel?: TicketListChannel[];
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
  const channel = search.channel;
  const channelKey = channel?.join(",") ?? "";
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
    channel: channel?.length ? channel : undefined,
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
  // Bulk change being prepared in the side panel; the selection bar's quick
  // actions prefill it. Nothing is sent before "apply".
  const [draft, setDraft] = useState<BulkDraft>({});
  const [panelOpen, setPanelOpen] = useState(false);
  const [openField, setOpenField] = useState<{ field: BulkField; seq: number } | null>(null);
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
    setDraft({});
    setPanelOpen(false);
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
    channelKey,
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
      // An open dropdown (e.g. in the bulk panel) consumes its own Escape.
      if (e.key === "Escape" && !e.defaultPrevented) {
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
  // Fetched with sort=deadline so the backend picks the N most urgent: with
  // sort=activity the most overdue tickets (usually the least active ones)
  // fell outside the page before the client ever re-sorted it.
  const showPinned =
    sort === "activity" && offset === 0 && !escalated && stateType !== "closed";
  const pinnedQ = useQuery({
    queryKey: ["tickets", "pinned", filterParams],
    queryFn: () =>
      api.listTickets({
        ...filterParams,
        escalating_within: ESCALATION_SOON_SECONDS,
        offset: 0,
        limit: PINNED_MAX,
        sort: "deadline",
        order: "asc",
      }),
    enabled: showPinned,
  });
  const pinnedData = pinnedQ.data?.items;
  const pinnedItems = useMemo(
    () =>
      showPinned
        ? (pinnedData ?? [])
            // The backend already filters by `escalating_within` and sorts by
            // deadline; re-check so the block can never fill up with tickets
            // that aren't due soon.
            .filter((t) => nearestDeadline(t) <= Date.now() / 1000 + ESCALATION_SOON_SECONDS)
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
    const states = statesQ.data ?? [];
    if (!draftReady(draft, states)) return;
    const ids = selectedIds;
    // One PATCH per ticket with every picked field: the backend applies them
    // in one transaction.
    const body: MutationRequest = draftToMutation(draft, states);

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
    label: stateLabel(t, s.name, s.name),
  }));
  const priorityItems: SelectMenuItem<number>[] = (prioritiesQ.data ?? []).map((p) => ({
    value: p.id,
    label: priorityLabel(t, p.name, p.name),
  }));
  const agentItems: SelectMenuItem<number>[] = (agentsQ.data ?? []).map((a) => ({
    value: a.id,
    label: a.full_name,
    hint: a.login,
  }));
  const bulkQueueItems: SelectMenuItem<number>[] = flattenQueues(queuesQ.data ?? [])
    .filter((q) => q.valid)
    .map((q) => ({ value: q.id, label: q.name }));

  // ── Bulk quick actions ─────────────────────────────────────────────────
  // Each one adds its change to the draft and opens the panel; pressed again
  // it takes the change back out. Closing the panel discards the draft.
  const bulkStates = statesQ.data ?? [];
  const closeState =
    bulkStates.find((s) => s.name === "closed successful") ??
    bulkStates.find((s) => s.type_name.startsWith("closed"));
  const pendingState =
    bulkStates.find((s) => s.name === "pending reminder") ??
    bulkStates.find((s) => s.type_name.startsWith("pending"));
  type QuickAction = "close" | "pending" | "mine" | "move";
  const quickOn: Record<QuickAction, boolean> = {
    close: isClosedStateId(bulkStates, draft.state_id),
    pending: isPendingStateId(bulkStates, draft.state_id),
    mine: user?.id != null && draft.owner_id === user.id,
    move: draft.queue_id != null,
  };
  const toggleQuick = (action: QuickAction) => {
    if (quickOn[action]) {
      const field: BulkField =
        action === "mine" ? "owner" : action === "move" ? "queue" : "state";
      setDraft(clearDraftField(draft, field));
      return;
    }
    if (action === "close" && closeState) {
      setDraft({ ...clearDraftField(draft, "state"), state_id: closeState.id });
    }
    if (action === "pending" && pendingState) {
      setDraft({
        ...draft,
        state_id: pendingState.id,
        pending_until: draft.pending_until ?? pendingQuickPicks()[0].value,
      });
    }
    if (action === "mine" && user?.id != null) setDraft({ ...draft, owner_id: user.id });
    if (action === "move") setOpenField({ field: "queue", seq: (openField?.seq ?? 0) + 1 });
    setPanelOpen(true);
  };
  const closePanel = () => {
    setPanelOpen(false);
    setDraft({});
  };
  const quickActions: { key: QuickAction; icon: ReactNode; label: string; disabled?: boolean }[] = [
    { key: "close", icon: <CheckIcon />, label: t("queue.bulk.quickClose"), disabled: !closeState },
    { key: "pending", icon: <ClockIcon />, label: t("queue.bulk.quickPending"), disabled: !pendingState },
    { key: "mine", icon: <UserIcon />, label: t("queue.bulk.quickMine"), disabled: user?.id == null },
    { key: "move", icon: <FolderIcon />, label: t("queue.bulk.quickMove") },
  ];
  const selectedKnown = [...pinnedItems, ...items.filter((i) => !pinnedIds.has(i.id))].filter((i) =>
    effectiveSelected.has(i.id),
  );

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
  const anyFlag =
    flagValue.escalated || flagValue.locked || flagValue.unassigned || Boolean(channel?.length);
  const toggleChannel = (key: TicketListChannel) => {
    const next = channel?.includes(key) ? channel.filter((c) => c !== key) : [...(channel ?? []), key];
    setSearch({ channel: next.length ? next : undefined, offset: 0 });
  };
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
          <ChannelChips
            active={channel ?? []}
            counts={facets?.channels}
            onToggle={toggleChannel}
          />
          {anyFlag && (
            <button
              type="button"
              data-testid="queue-flag-reset"
              onClick={() =>
                setSearch({
                  escalated: undefined,
                  locked: undefined,
                  unassigned: undefined,
                  channel: undefined,
                  offset: 0,
                })
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
                  {quickActions.map((action) => (
                    <button
                      key={action.key}
                      type="button"
                      aria-pressed={quickOn[action.key]}
                      disabled={noSelection || action.disabled}
                      data-testid={`queue-bulk-quick-${action.key}`}
                      onClick={() => toggleQuick(action.key)}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:h-3.5 [&_svg]:w-3.5",
                        quickOn[action.key]
                          ? "border-accent bg-accent-dim text-accent"
                          : "border-hairline bg-surface text-ink hover:bg-surface-subtle",
                      )}
                    >
                      {action.icon}
                      {action.label}
                    </button>
                  ))}
                  <button
                    type="button"
                    aria-expanded={panelOpen}
                    disabled={noSelection}
                    data-testid="queue-bulk-all-fields"
                    onClick={() => (panelOpen ? closePanel() : setPanelOpen(true))}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:h-3.5 [&_svg]:w-3.5",
                      panelOpen
                        ? "border-accent bg-accent-dim text-accent"
                        : "border-hairline bg-surface text-ink hover:bg-surface-subtle",
                    )}
                  >
                    <PencilIcon />
                    {t("queue.bulk.allFields")}
                  </button>
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
                  // Same set as the block, in the same order: the whole list
                  // by nearest deadline. The "Eskaliert" chip would drop the
                  // due-soon tickets the block's count includes.
                  onShowAll: () => setSearch({ sort: "deadline", order: "asc", offset: 0 }),
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

      {hasSelection && panelOpen && (
        <BulkEditPanel
          count={selectedIds.length}
          known={selectedKnown}
          refs={{
            states: bulkStates,
            priorities: prioritiesQ.data ?? [],
            agents: agentItems,
            queues: bulkQueueItems,
          }}
          draft={draft}
          onChange={setDraft}
          meId={user?.id}
          applying={applying}
          onApply={() => void applyBulkAction()}
          onDiscard={() => setDraft({})}
          onClose={closePanel}
          openField={openField}
          className="sticky top-0 max-h-screen w-[340px] shrink-0 self-start overflow-y-auto max-lg:fixed max-lg:inset-y-0 max-lg:right-0 max-lg:z-30 max-lg:max-h-none max-lg:shadow-2xl"
        />
      )}
    </div>
  );
}

/**
 * Channel filter chips next to the "Nur" flags. Several can be active (any of
 * them). The group only shows once a chat channel has tickets in the current
 * scope — an e-mail-only install sees no "E-Mail 345" chip — and a chat
 * channel without tickets (web chat before it goes live) stays hidden.
 */
function ChannelChips({
  active,
  counts,
  onToggle,
}: {
  active: TicketListChannel[];
  counts: Record<TicketListChannel, number> | undefined;
  onToggle: (key: TicketListChannel) => void;
}) {
  const { t } = useTranslation();
  const visible = TICKET_CHANNEL_KEYS.filter(
    (key) => active.includes(key) || key === "email" || (counts?.[key] ?? 0) > 0,
  );
  const anyChat = TICKET_CHANNEL_KEYS.some((k) => k !== "email" && (counts?.[k] ?? 0) > 0);
  if (!anyChat && active.length === 0) return null;
  return (
    <span
      className="inline-flex flex-wrap items-center gap-1.5"
      role="group"
      aria-label={t("queue.channel.label")}
      data-testid="queue-channel-chips"
    >
      <span aria-hidden className="mx-1 h-4 w-px bg-hairline" />
      <span className="mr-0.5 text-[12px] text-muted">{t("queue.channel.label")}</span>
      {visible.map((key) => {
        const meta = TICKET_CHANNELS[key];
        const Icon = meta.icon;
        const on = active.includes(key);
        const count = counts?.[key];
        return (
          <button
            key={key}
            type="button"
            aria-pressed={on}
            data-testid={`queue-channel-${key}`}
            onClick={() => onToggle(key)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[12px] transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
              on
                ? "border-accent/50 bg-accent-dim text-accent"
                : "border-hairline bg-surface text-muted hover:border-muted hover:text-ink",
            )}
          >
            <Icon className={cn("h-3.5 w-3.5", !on && meta.iconCls)} />
            {t(meta.labelKey)}
            {count != null && <span className="font-mono text-[10.5px] tabular-nums">{count}</span>}
          </button>
        );
      })}
    </span>
  );
}
