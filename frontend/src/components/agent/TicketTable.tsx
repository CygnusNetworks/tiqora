import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { Link, useNavigate } from "@tanstack/react-router";
import type { MutationRequest, TicketListItem } from "@/lib/api";
import { dayBucket, formatDateTime, formatListTime, type DayBucket } from "@/lib/format";
import { senderDisplayName } from "@/lib/articleChannel";
import { cn } from "@/lib/cn";
import { setTicketNavContext, type TicketNavContext } from "@/lib/ticketNavContext";
import { Button } from "@/components/ui/Button";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { Spinner } from "@/components/ui/Spinner";
import { PriorityChip, StateChip } from "@/components/ui/StatusChip";
import { LockIcon } from "@/components/ui/icons";
import {
  combinedEscalationLevel,
  formatCountdown,
  spineClassName,
  stateColorVar,
  type EscalationLevel,
} from "@/lib/status";

export type SortKey =
  | "activity"
  | "tn"
  | "title"
  | "state"
  | "priority"
  | "owner"
  | "customer"
  | "age"
  | "changed";

/** How far ahead an SLA deadline counts as "due soon" in the list — the amber
 * spine/badge and the pinned "needs attention" block use the same window. */
export const ESCALATION_SOON_SECONDS = 2 * 60 * 60;

/** Znuny's stock "3 normal" priority. Only deviations are worth a chip in a
 * list where nearly every ticket is normal. */
const NORMAL_PRIORITY_ID = 3;

export type TicketTableSelection = {
  selected: Set<number>;
  /** `range` is true for a shift-click: select everything between the last
   * toggled row and this one (in display order). */
  onToggleRow: (id: number, range?: boolean) => void;
  onToggleAllPage: () => void;
  allPageSelected: boolean;
  somePageSelected: boolean;
};

/** Inline per-row quick edit for state/priority/owner — clicking one of those
 * cells opens a `SelectMenu` that patches just that ticket. Reference option
 * lists (states/priorities/agents) are owned by the caller (QueuesPage) and
 * fetched lazily: `onRequestOptions` fires the first time any row's menu
 * opens, letting the caller flip a query's `enabled` flag rather than every
 * row firing its own request on mount. */
export type TicketQuickEdit = {
  stateItems: SelectMenuItem<number>[];
  priorityItems: SelectMenuItem<number>[];
  agentItems: SelectMenuItem<number>[];
  agentsLoading?: boolean;
  onPatch: (ticketId: number, body: MutationRequest) => void;
  onRequestOptions: () => void;
};

/** Tickets shown in a separate block above the list regardless of sort
 * order — overdue / due-soon SLA deadlines in the inbox. */
export type TicketTablePinned = {
  items: TicketListItem[];
  /** Total number of matches; more than `items.length` shows a "show all" link. */
  total: number;
  onShowAll?: () => void;
};

export type TicketTableProps = {
  items: TicketListItem[];
  total: number;
  offset: number;
  limit: number;
  sort: SortKey;
  order: "asc" | "desc";
  isLoading?: boolean;
  onSortChange: (sort: SortKey, order: "asc" | "desc") => void;
  onPageChange: (offset: number) => void;
  /** Opt-in row checkboxes. They stay out of the way (visible on hover) until
   * something is selected; the row itself still opens the ticket. */
  selection?: TicketTableSelection;
  /** Opt-in inline quick edit for state/priority/owner cells. */
  quickEdit?: TicketQuickEdit;
  /** Fired when the agent clicks a ticket's customer cell (number/email),
   * with that ticket's `customer_id`. Filtering happens client-side (caller
   * updates its own search state) — the cell is only clickable when a
   * `customer_id` is present, since that's what the list endpoint filters on. */
  onCustomerClick?: (customerId: string) => void;
  /** Group rows under Heute / Gestern / Diese Woche / Älter headers. Only
   * meaningful while the list is sorted by activity. */
  groupByDay?: boolean;
  pinned?: TicketTablePinned;
  /** Omit the per-row queue name (e.g. inside a single-queue view). */
  hideQueue?: boolean;
  /** Where this list lives, so the opened ticket can link back to it and
   * step through the same rows with ‹ › (see `ticketNavContext`). */
  navContext?: Omit<TicketNavContext, "ids">;
};

const SORT_COLUMNS: { key: SortKey; labelKey: string }[] = [
  { key: "activity", labelKey: "ticket.list.lastActivity" },
  { key: "tn", labelKey: "ticket.list.ticket" },
];

/* Header cells and data cells share this exact column template so every
   cell lines up under its header (grid rows, not <table>: the status spine
   is a pseudo-element some browsers exclude from table column sizing). */
const GRID_COLS = "84px minmax(200px,1fr) minmax(100px,140px) 136px";
const SELECT_GRID_COLS = `18px ${GRID_COLS}`;

/** When the ticket last saw activity: the newest article, else the ticket's
 * own change time (tickets without articles, older API responses). */
function activityTime(ticket: TicketListItem): string {
  return ticket.last_article_time ?? ticket.change_time;
}

function escalationEpochs(ticket: TicketListItem): number[] {
  return [
    ticket.escalation_time,
    ticket.escalation_response_time,
    ticket.escalation_update_time,
    ticket.escalation_solution_time,
  ];
}

/** The nearest SLA deadline that is set, or null. */
function nearestEscalation(ticket: TicketListItem): number | null {
  const set = escalationEpochs(ticket).filter((e) => e > 0);
  return set.length ? Math.min(...set) : null;
}

type Row = { kind: "row"; ticket: TicketListItem } | { kind: "head"; key: string; node: ReactNode };

export function TicketTable({
  items,
  total,
  offset,
  limit,
  sort,
  order,
  isLoading,
  onSortChange,
  onPageChange,
  selection,
  quickEdit,
  onCustomerClick,
  groupByDay,
  pinned,
  hideQueue,
  navContext,
}: TicketTableProps) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [focusIdx, setFocusIdx] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const locale = toBcp47(i18n.language);
  const gridCols = selection ? SELECT_GRID_COLS : GRID_COLS;
  const selecting = (selection?.selected.size ?? 0) > 0;

  // Pinned tickets are shown once, in their own block — drop them from the
  // regular list below so a ticket never appears twice on the page.
  const pinnedList = pinned?.items;
  const { pinnedItems, listItems, ordered } = useMemo(() => {
    const pinnedItems = pinnedList ?? [];
    const pinnedIds = new Set(pinnedItems.map((p) => p.id));
    const listItems = pinnedIds.size ? items.filter((it) => !pinnedIds.has(it.id)) : items;
    return { pinnedItems, listItems, ordered: [...pinnedItems, ...listItems] };
  }, [items, pinnedList]);

  useEffect(() => {
    setFocusIdx(0);
  }, [items]);

  const openTicket = (id: number) => {
    if (navContext) setTicketNavContext({ ...navContext, ids: ordered.map((it) => it.id) });
    void navigate({ to: "/agent/tickets/$ticketId", params: { ticketId: String(id) } });
  };
  // The keyboard handler is subscribed once per list change; read the latest
  // opener through a ref instead of re-subscribing on every render.
  const openTicketRef = useRef(openTicket);
  openTicketRef.current = openTicket;

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;

    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "j") {
        e.preventDefault();
        setFocusIdx((i) => Math.min(i + 1, Math.max(ordered.length - 1, 0)));
      } else if (e.key === "k") {
        e.preventDefault();
        setFocusIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === " " && selection && ordered[focusIdx]) {
        e.preventDefault();
        selection.onToggleRow(ordered[focusIdx].id);
      } else if (e.key === "Enter" && ordered[focusIdx]) {
        e.preventDefault();
        openTicketRef.current(ordered[focusIdx].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ordered, focusIdx, selection]);

  const toggleSort = (key: SortKey) => {
    if (sort === key) {
      onSortChange(key, order === "asc" ? "desc" : "asc");
    } else {
      onSortChange(key, key === "age" || key === "changed" || key === "activity" ? "desc" : "asc");
    }
  };

  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));

  const rows: Row[] = [];
  if (pinnedItems.length) {
    rows.push({
      kind: "head",
      key: "pinned",
      node: (
        <div
          className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-hairline bg-amber/10 px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-amber"
          data-testid="ticket-table-pinned-head"
        >
          <span>{t("queue.pinned.title", { count: pinned?.total ?? pinnedItems.length })}</span>
          <span className="font-normal normal-case tracking-normal text-muted">
            {t("queue.pinned.hint")}
          </span>
          {pinned && pinned.total > pinnedItems.length && pinned.onShowAll && (
            <button
              type="button"
              onClick={pinned.onShowAll}
              className="ml-auto font-medium normal-case tracking-normal text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              data-testid="ticket-table-pinned-show-all"
            >
              {t("queue.pinned.showAll", { count: pinned.total })}
            </button>
          )}
        </div>
      ),
    });
    for (const ticket of pinnedItems) rows.push({ kind: "row", ticket });
  }
  let lastBucket: DayBucket | null = null;
  for (const ticket of listItems) {
    if (groupByDay) {
      const bucket = dayBucket(activityTime(ticket));
      if (bucket !== lastBucket) {
        lastBucket = bucket;
        rows.push({
          kind: "head",
          key: `day-${bucket}`,
          node: (
            <div
              className="border-b border-hairline bg-surface px-4 pt-3 pb-1.5 text-[10.5px] font-semibold uppercase tracking-wide text-muted"
              data-testid={`ticket-table-day-${bucket}`}
            >
              {t(`queue.day.${bucket}`)}
            </div>
          ),
        });
      }
    } else if (pinnedItems.length && lastBucket === null) {
      lastBucket = "older";
      rows.push({
        kind: "head",
        key: "rest",
        node: (
          <div className="border-b border-hairline bg-surface-subtle px-4 py-1.5 text-[10.5px] font-semibold uppercase tracking-wide text-muted">
            {t("queue.pinned.rest")}
          </div>
        ),
      });
    }
    rows.push({ kind: "row", ticket });
  }

  return (
    <div className="flex flex-col gap-2" data-testid="ticket-table" ref={rootRef}>
      <div
        className="overflow-hidden rounded-lg border border-hairline bg-surface"
        role="table"
      >
        {/* Header row — desktop/tablet only; mobile uses cards below. */}
        <div
          role="row"
          className="hidden items-center gap-3 border-b border-hairline bg-surface-subtle py-2 pr-4 pl-4 text-[11px] font-medium text-muted md:grid"
          style={{ gridTemplateColumns: gridCols }}
        >
          {selection && (
            <input
              type="checkbox"
              checked={selection.allPageSelected}
              ref={(el) => {
                if (el) el.indeterminate = selection.somePageSelected;
              }}
              onChange={selection.onToggleAllPage}
              data-testid="queue-select-all-page"
              aria-label={t("queue.bulk.selectAllOnPage")}
              className="h-3.5 w-3.5 accent-accent"
            />
          )}
          {SORT_COLUMNS.map((col) => (
            <button
              key={col.key}
              type="button"
              role="columnheader"
              className={cn(
                "inline-flex items-center gap-1 text-left hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
                sort === col.key && "text-ink",
              )}
              onClick={() => toggleSort(col.key)}
              data-testid={`ticket-table-sort-${col.key}`}
            >
              {t(col.labelKey)}
              {sort === col.key && <span aria-hidden>{order === "asc" ? "↑" : "↓"}</span>}
            </button>
          ))}
          <span role="columnheader">{t("ticket.owner")}</span>
          <span role="columnheader">{t("ticket.state")}</span>
        </div>

        {isLoading && ordered.length === 0 && (
          <div className="px-3 py-8 text-center text-muted">
            <Spinner className="mx-auto" />
          </div>
        )}
        {!isLoading && ordered.length === 0 && (
          <div className="px-3 py-8 text-center text-muted" data-testid="ticket-table-empty">
            {t("ticket.noTickets")}
          </div>
        )}

        {rows.map((row) => {
          if (row.kind === "head") return <div key={row.key}>{row.node}</div>;
          const ticket = row.ticket;
          const idx = ordered.indexOf(ticket);
          return (
            <TicketRow
              key={ticket.id}
              ticket={ticket}
              gridCols={gridCols}
              focused={idx === focusIdx}
              locale={locale}
              selection={selection}
              selecting={selecting}
              quickEdit={quickEdit}
              onCustomerClick={onCustomerClick}
              hideQueue={hideQueue}
              onHover={() => setFocusIdx(idx)}
              onOpen={() => openTicket(ticket.id)}
            />
          );
        })}
      </div>
      <div className="flex items-center justify-between text-xs text-muted">
        <span>
          {t("ticket.pagination", {
            from: total === 0 ? 0 : offset + 1,
            to: Math.min(offset + limit, total),
            total,
          })}
        </span>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            disabled={offset <= 0}
            onClick={() => onPageChange(Math.max(0, offset - limit))}
          >
            {t("common.prev")}
          </Button>
          <span className="tabular-nums px-2">
            {page}/{pages}
          </span>
          <Button
            size="sm"
            variant="ghost"
            disabled={offset + limit >= total}
            onClick={() => onPageChange(offset + limit)}
          >
            {t("common.next")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function TicketRow({
  ticket,
  gridCols,
  focused,
  locale,
  selection,
  selecting,
  quickEdit,
  onCustomerClick,
  hideQueue,
  onHover,
  onOpen,
}: {
  ticket: TicketListItem;
  gridCols: string;
  focused: boolean;
  locale: string;
  selection?: TicketTableSelection;
  selecting: boolean;
  quickEdit?: TicketQuickEdit;
  onCustomerClick?: (customerId: string) => void;
  hideQueue?: boolean;
  onHover: () => void;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  const escLevel: EscalationLevel = combinedEscalationLevel(
    escalationEpochs(ticket),
    ESCALATION_SOON_SECONDS,
  );
  const spineColor = escLevel === "none" ? stateColorVar(ticket.state) : undefined;
  const nearest = nearestEscalation(ticket);
  const isSelected = selection?.selected.has(ticket.id) ?? false;
  const attachmentCount = ticket.attachment_count ?? 0;
  const hasAiSummary = ticket.has_ai_summary ?? false;
  const aiReplySource = ticket.ai_reply_source ?? null;
  const locked = ticket.lock === "lock" || ticket.lock === "tmp_lock";
  const lastSender = ticket.last_sender_type ?? null;
  const when = activityTime(ticket);

  const customerNumber = ticket.customer_id || null;
  const customerLogin = ticket.customer_user_id || null;
  // `customer_email` comes from the customer_user record; customer_user_id
  // (the login) is often the e-mail address itself, so fall back to it
  // when the record has no separate email on file.
  const customerEmail =
    ticket.customer_email || (customerLogin?.includes("@") ? customerLogin : null);
  const customerLabel = customerNumber || customerLogin;
  const showCustomerEmail = Boolean(customerEmail) && customerEmail !== customerLabel;
  // Filtering is by `customer_id` (the backend's exact-match filter
  // param), so the cell is only clickable when that field is set —
  // a customer_user_id-only ticket has nothing to filter on.
  const canFilterByCustomer = Boolean(onCustomerClick && customerNumber);
  const senderFallback = !customerLabel ? senderDisplayName(ticket.first_from) : null;

  const escalationBadge = escLevel !== "none" && nearest != null && (
    <span
      className={cn(
        "flex-none whitespace-nowrap rounded px-1.5 py-px font-mono text-[10px] font-semibold tabular-nums",
        escLevel === "breached" ? "bg-danger/15 text-danger" : "bg-amber/15 text-amber",
      )}
      title={formatDateTime(new Date(nearest * 1000), locale)}
      data-testid={`ticket-escalation-badge-${ticket.id}`}
      data-level={escLevel}
    >
      {t("ticket.slaCountdown", { value: formatCountdown(nearest) })}
    </span>
  );

  const priorityChip = ticket.priority_id !== NORMAL_PRIORITY_ID && (
    <PriorityChip
      priority={ticket.priority}
      priorityId={ticket.priority_id}
      empty="—"
      className="flex-none"
      data-testid={`ticket-priority-chip-${ticket.id}`}
    />
  );

  const stateChip = (
    <StateChip
      state={ticket.state}
      empty="—"
      className="max-w-full truncate"
      data-testid={`ticket-state-chip-${ticket.id}`}
    />
  );
  const ownerLabel = ticket.owner_name || ticket.owner_login || "—";

  return (
    <div
      role="row"
      data-testid={`ticket-row-${ticket.id}`}
      className={cn(
        "group relative flex cursor-pointer flex-col gap-1.5 border-b border-hairline py-2.5 pr-4 pl-4 transition-colors duration-100 last:border-b-0 hover:bg-surface-subtle md:grid md:items-center md:gap-3 md:py-2",
        spineClassName(escLevel),
        isSelected && "bg-accent-dim hover:bg-accent-dim",
        focused && !isSelected && "bg-surface-subtle ring-1 ring-inset ring-accent/40",
      )}
      style={
        {
          "--spine-color": spineColor,
          gridTemplateColumns: gridCols,
        } as CSSProperties
      }
      onClick={onOpen}
      onMouseEnter={onHover}
    >
      {selection && (
        <span className="hidden items-center md:inline-flex">
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => undefined}
            onClick={(e) => {
              e.stopPropagation();
              selection.onToggleRow(ticket.id, e.shiftKey);
            }}
            data-testid={`queue-row-check-${ticket.id}`}
            aria-label={t("queue.bulk.selectRow")}
            className={cn(
              "h-3.5 w-3.5 accent-accent transition-opacity duration-100 focus-visible:opacity-100",
              isSelected || selecting ? "opacity-100" : "opacity-0 group-hover:opacity-100",
            )}
          />
        </span>
      )}

      {/* Last activity: when, and whose turn it was (arrow into / out of the house). */}
      <span
        className="flex items-center gap-2 font-mono text-[11.5px] tabular-nums text-muted md:flex-col md:items-start md:gap-0"
        title={formatDateTime(when, locale)}
        data-testid={`ticket-activity-${ticket.id}`}
      >
        {selection && (
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => undefined}
            onClick={(e) => {
              e.stopPropagation();
              selection.onToggleRow(ticket.id, e.shiftKey);
            }}
            data-testid={`queue-row-check-mobile-${ticket.id}`}
            aria-label={t("queue.bulk.selectRow")}
            className="h-3.5 w-3.5 accent-accent md:hidden"
          />
        )}
        <span className="text-ink/90">{formatListTime(when, locale, t("queue.day.yesterday"))}</span>
        {lastSender && (
          <span
            className={cn("text-[10.5px]", lastSender === "customer" && "text-accent")}
            data-testid={`ticket-last-sender-${ticket.id}`}
            data-sender={lastSender}
          >
            {t(`ticket.list.lastBy.${lastSender}`, { defaultValue: lastSender })}
          </span>
        )}
      </span>

      <span className="min-w-0">
        <span className="flex min-w-0 items-center gap-1.5">
          <span
            className="min-w-0 truncate text-[13.5px] font-medium text-ink"
            title={ticket.title ?? ""}
          >
            {ticket.title || "—"}
          </span>
          {escalationBadge}
          {priorityChip}
          {locked && (
            <span
              className="flex-none text-muted"
              title={t("ticket.list.locked")}
              data-testid={`ticket-lock-indicator-${ticket.id}`}
            >
              <LockIcon className="h-3 w-3" />
            </span>
          )}
          {ticket.archive_flag === 1 && (
            <span
              className="flex-none rounded bg-surface-subtle px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted"
              data-testid={`ticket-archived-badge-${ticket.id}`}
            >
              {t("queue.archivedBadge")}
            </span>
          )}
          {attachmentCount > 0 && (
            <span
              className="inline-flex flex-none items-center gap-0.5 font-mono text-[10.5px] tabular-nums text-muted"
              title={t("ticket.list.attachments", { count: attachmentCount })}
              data-testid={`ticket-attachment-indicator-${ticket.id}`}
            >
              <svg
                viewBox="0 0 16 16"
                className="h-3 w-3"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
                aria-hidden
              >
                <path d="M10.5 4.5 5.9 9.1a1.6 1.6 0 0 0 2.3 2.3l5-5a3.1 3.1 0 0 0-4.4-4.4l-5 5a4.6 4.6 0 0 0 6.5 6.5l4.2-4.2" />
              </svg>
              {attachmentCount}
            </span>
          )}
          {hasAiSummary && (
            <span
              className="flex-none text-[11px] text-accent/80"
              title={t("ticket.list.hasSummary")}
              data-testid={`ticket-summary-indicator-${ticket.id}`}
              aria-label={t("ticket.list.hasSummary")}
            >
              ✦
            </span>
          )}
          {aiReplySource && (
            // Same glyph for both, because both mean "the AI wrote
            // this"; the tone separates a reply the agent sent by
            // itself from a draft a human reviewed and accepted.
            <span
              className={cn(
                "flex-none text-[11px]",
                aiReplySource === "auto" ? "text-accent" : "text-muted",
              )}
              title={
                aiReplySource === "auto"
                  ? t("ticket.list.aiRepliedAuto")
                  : t("ticket.list.aiRepliedAccepted")
              }
              aria-label={
                aiReplySource === "auto"
                  ? t("ticket.list.aiRepliedAuto")
                  : t("ticket.list.aiRepliedAccepted")
              }
              data-testid={`ticket-ai-reply-indicator-${ticket.id}`}
              data-source={aiReplySource}
            >
              🤖
            </span>
          )}
        </span>
        {/* Meta line: ticket number · customer · queue. The click-to-filter
            handler sits on a shrink-to-fit inline block, NOT a full-width
            cell, so clicks right of the customer name still open the ticket. */}
        <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[11.5px] text-muted">
          <span className="flex-none font-mono text-[11px] tabular-nums text-accent">{ticket.tn}</span>
          <span aria-hidden>·</span>
          <span className="min-w-0 truncate" data-testid={`ticket-customer-cell-${ticket.id}`}>
            {customerLabel ? (
              <span
                className={cn(
                  "inline",
                  canFilterByCustomer && "cursor-pointer hover:text-ink hover:underline",
                )}
                data-testid={`ticket-customer-name-${ticket.id}`}
                title={canFilterByCustomer ? t("ticket.filterByCustomer") : customerLabel}
                onClick={
                  canFilterByCustomer
                    ? (e) => {
                        e.stopPropagation();
                        if (customerNumber) onCustomerClick?.(customerNumber);
                      }
                    : undefined
                }
              >
                {customerLabel}
                {showCustomerEmail && (
                  <span
                    className="text-muted/70"
                    title={customerEmail ?? undefined}
                    data-testid={`ticket-customer-email-${ticket.id}`}
                  >
                    {" "}
                    {customerEmail}
                  </span>
                )}
              </span>
            ) : senderFallback ? (
              <span
                className="italic"
                title={t("ticket.senderNoCustomer")}
                data-testid={`ticket-sender-fallback-${ticket.id}`}
              >
                ✉ {senderFallback}
              </span>
            ) : (
              "—"
            )}
          </span>
          {!hideQueue && ticket.queue_name && (
            <>
              <span aria-hidden>·</span>
              <Link
                to="/agent/queues"
                search={{ queue_id: ticket.queue_id }}
                onClick={(e) => e.stopPropagation()}
                className="flex-none truncate font-mono text-[10.5px] text-muted transition-colors duration-100 hover:text-accent"
                title={ticket.queue_name}
                data-testid={`ticket-queue-chip-${ticket.id}`}
              >
                {ticket.queue_name}
              </Link>
            </>
          )}
        </span>
      </span>

      <span className="hidden min-w-0 items-center md:inline-flex">
        {quickEdit ? (
          <QuickEditTrigger
            testId={`ticket-row-owner-${ticket.id}`}
            panelTestId={`ticket-row-owner-menu-${ticket.id}`}
            items={quickEdit.agentItems}
            value={ticket.owner_id}
            loading={quickEdit.agentsLoading}
            searchThreshold={8}
            onOpen={quickEdit.onRequestOptions}
            onSelect={(id) => quickEdit.onPatch(ticket.id, { owner_id: id })}
            placeholder={t("ticket.dialog.selectPlaceholder")}
          >
            <span className="max-w-full truncate text-[12.5px] text-ink/80">{ownerLabel}</span>
          </QuickEditTrigger>
        ) : (
          <span className="max-w-full truncate text-[12.5px] text-ink/80">{ownerLabel}</span>
        )}
      </span>
      <span className="hidden min-w-0 items-center md:inline-flex">
        {quickEdit ? (
          <QuickEditTrigger
            testId={`ticket-row-state-${ticket.id}`}
            panelTestId={`ticket-row-state-menu-${ticket.id}`}
            items={quickEdit.stateItems}
            value={ticket.state_id}
            onOpen={quickEdit.onRequestOptions}
            onSelect={(id) => quickEdit.onPatch(ticket.id, { state_id: id })}
            placeholder={t("ticket.dialog.selectPlaceholder")}
          >
            {stateChip}
          </QuickEditTrigger>
        ) : (
          stateChip
        )}
      </span>

      {/* Mobile card footer: state + owner */}
      <div className="flex items-center justify-between gap-2 text-[11.5px] text-muted md:hidden">
        <StateChip state={ticket.state} data-testid={`ticket-state-chip-mobile-${ticket.id}`} />
        <span className="shrink-0 truncate">{ownerLabel}</span>
      </div>
    </div>
  );
}

/** Wraps a cell's existing chip/text in a `SelectMenu` trigger for the
 * per-row quick edit — clicking opens the listbox and patches just this
 * ticket, without navigating to it or (in a table row) toggling selection.
 * `stopPropagation` on both click and keydown is what keeps the row's own
 * click/navigate handler from also firing. */
function QuickEditTrigger<T extends string | number>({
  items,
  value,
  loading,
  searchThreshold,
  placeholder,
  testId,
  panelTestId,
  onOpen,
  onSelect,
  children,
}: {
  items: SelectMenuItem<T>[];
  value: T | null | undefined;
  loading?: boolean;
  searchThreshold?: number;
  placeholder: string;
  testId: string;
  panelTestId: string;
  onOpen: () => void;
  onSelect: (value: T) => void;
  children: ReactNode;
}) {
  return (
    <SelectMenu
      items={items}
      value={value ?? undefined}
      loading={loading}
      searchThreshold={searchThreshold}
      onSelect={onSelect}
      placeholder={placeholder}
      panelTestId={panelTestId}
      trigger={({ ref, toggleProps }) => (
        <button
          ref={ref}
          type="button"
          data-testid={testId}
          aria-haspopup={toggleProps["aria-haspopup"]}
          aria-expanded={toggleProps["aria-expanded"]}
          onClick={(e) => {
            e.stopPropagation();
            onOpen();
            toggleProps.onClick();
          }}
          onKeyDown={(e) => {
            e.stopPropagation();
            toggleProps.onKeyDown(e);
          }}
          className="max-w-full truncate rounded text-left transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        >
          {children}
        </button>
      )}
    />
  );
}
