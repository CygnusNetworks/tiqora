import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "@tanstack/react-router";
import type { TicketListItem } from "@/lib/api";
import { formatAgeSeconds, formatDateTime, isEscalated } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { PriorityChip, StateChip } from "@/components/ui/StatusChip";
import {
  combinedEscalationLevel,
  formatCountdown,
  spineClassName,
  stateColorVar,
} from "@/lib/status";

export type SortKey =
  | "tn"
  | "title"
  | "state"
  | "priority"
  | "owner"
  | "customer"
  | "age"
  | "changed";

export type TicketTableSelection = {
  selected: Set<number>;
  onToggleRow: (id: number) => void;
  onToggleAllPage: () => void;
  allPageSelected: boolean;
  somePageSelected: boolean;
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
  /** Opt-in row-checkbox selection mode — row click toggles rather than navigates. */
  selection?: TicketTableSelection;
};

const SORT_COLUMNS: { key: SortKey; labelKey: string }[] = [
  { key: "tn", labelKey: "ticket.tn" },
  { key: "title", labelKey: "ticket.title" },
  { key: "state", labelKey: "ticket.state" },
  { key: "priority", labelKey: "ticket.priority" },
  { key: "owner", labelKey: "ticket.owner" },
  { key: "age", labelKey: "ticket.age" },
];

/* Header cells and data cells share this exact column template so the first
   data cell (Ticket#) always lines up under its header — the previous
   <table> layout drifted because the status spine was an absolutely
   positioned pseudo-element on <tr>, which some browsers exclude from
   table column-width calculation. Grid rows don't have that failure mode. */
const GRID_COLS =
  "minmax(120px,136px) minmax(180px,2.2fr) 96px 96px minmax(90px,120px) 84px";
const SELECT_GRID_COLS = `28px ${GRID_COLS}`;

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
}: TicketTableProps) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [focusIdx, setFocusIdx] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const gridCols = selection ? SELECT_GRID_COLS : GRID_COLS;

  useEffect(() => {
    setFocusIdx(0);
  }, [items]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;

    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "j") {
        e.preventDefault();
        setFocusIdx((i) => Math.min(i + 1, Math.max(items.length - 1, 0)));
      } else if (e.key === "k") {
        e.preventDefault();
        setFocusIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === " " && selection && items[focusIdx]) {
        e.preventDefault();
        selection.onToggleRow(items[focusIdx].id);
      } else if (e.key === "Enter" && items[focusIdx]) {
        e.preventDefault();
        if (selection) {
          selection.onToggleRow(items[focusIdx].id);
          return;
        }
        void navigate({
          to: "/agent/tickets/$ticketId",
          params: { ticketId: String(items[focusIdx].id) },
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, focusIdx, navigate, selection]);

  const toggleSort = (key: SortKey) => {
    if (sort === key) {
      onSortChange(key, order === "asc" ? "desc" : "asc");
    } else {
      onSortChange(key, key === "age" || key === "changed" ? "desc" : "asc");
    }
  };

  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));

  return (
    <div className="flex flex-col gap-2" data-testid="ticket-table" ref={rootRef}>
      <div
        className="overflow-hidden rounded-lg border border-hairline bg-surface"
        role="table"
      >
        {/* Header row — desktop/tablet only; mobile uses cards below. */}
        <div
          role="row"
          className="hidden items-center gap-3 border-b border-hairline bg-surface-subtle px-4 py-2.5 text-[10.5px] font-semibold uppercase tracking-wide text-muted md:grid"
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
              className="rounded border-hairline text-accent focus:ring-accent"
            />
          )}
          {SORT_COLUMNS.map((col) => (
            <button
              key={col.key}
              type="button"
              role="columnheader"
              className="inline-flex items-center gap-1 text-left hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              onClick={() => toggleSort(col.key)}
            >
              {t(col.labelKey)}
              {sort === col.key && <span aria-hidden>{order === "asc" ? "↑" : "↓"}</span>}
            </button>
          ))}
        </div>

        {isLoading && items.length === 0 && (
          <div className="px-3 py-8 text-center text-muted">
            <Spinner className="mx-auto" />
          </div>
        )}
        {!isLoading && items.length === 0 && (
          <div className="px-3 py-8 text-center text-muted" data-testid="ticket-table-empty">
            {t("ticket.noTickets")}
          </div>
        )}

        {items.map((ticket, idx) => {
          const escLevel = combinedEscalationLevel([
            ticket.escalation_time,
            ticket.escalation_response_time,
            ticket.escalation_update_time,
            ticket.escalation_solution_time,
          ]);
          const esc =
            isEscalated(ticket.escalation_time) ||
            isEscalated(ticket.escalation_response_time) ||
            isEscalated(ticket.escalation_update_time) ||
            isEscalated(ticket.escalation_solution_time);
          const spineColor = escLevel === "none" ? stateColorVar(ticket.state) : undefined;
          const escalationBadge = esc && (
            <span
              className="ml-1.5 whitespace-nowrap rounded-md bg-amber px-2 py-0.5 font-mono text-[10px] font-semibold tabular-nums text-[#0E1015]"
              title={t("ticket.escalated")}
              data-testid={`ticket-escalation-badge-${ticket.id}`}
            >
              {t("ticket.slaCountdown", {
                value: formatCountdown(
                  ticket.escalation_time ??
                    ticket.escalation_response_time ??
                    ticket.escalation_update_time ??
                    ticket.escalation_solution_time,
                ),
              })}
            </span>
          );

          return (
            <div
              key={ticket.id}
              role="row"
              data-testid={`ticket-row-${ticket.id}`}
              className={cn(
                "relative flex cursor-pointer flex-col gap-1.5 border-b border-hairline px-4 py-2.5 transition-colors duration-100 last:border-b-0 hover:bg-surface-subtle md:grid md:items-center md:gap-3 md:py-[7px]",
                spineClassName(escLevel),
                idx === focusIdx && "bg-surface-subtle ring-1 ring-inset ring-accent/40",
              )}
              style={
                {
                  "--spine-color": spineColor,
                  gridTemplateColumns: gridCols,
                } as CSSProperties
              }
              onClick={() => {
                if (selection) {
                  selection.onToggleRow(ticket.id);
                  return;
                }
                void navigate({
                  to: "/agent/tickets/$ticketId",
                  params: { ticketId: String(ticket.id) },
                });
              }}
              onMouseEnter={() => setFocusIdx(idx)}
            >
              {/* Mobile card header: selection checkbox + TN + age */}
              <div className="flex items-center justify-between gap-2 md:hidden">
                <span className="flex items-center gap-2">
                  {selection && (
                    <input
                      type="checkbox"
                      checked={selection.selected.has(ticket.id)}
                      onChange={() => selection.onToggleRow(ticket.id)}
                      onClick={(e) => e.stopPropagation()}
                      data-testid={`queue-row-check-mobile-${ticket.id}`}
                      aria-label={t("queue.bulk.selectRow")}
                      className="rounded border-hairline text-accent focus:ring-accent"
                    />
                  )}
                  <span className="font-mono text-xs tabular-nums text-accent">{ticket.tn}</span>
                </span>
                <span className="font-mono text-[11px] tabular-nums text-muted">
                  {formatAgeSeconds(ticket.age_seconds, locale)}
                </span>
              </div>

              {selection && (
                <span className="hidden items-center md:inline-flex">
                  <input
                    type="checkbox"
                    checked={selection.selected.has(ticket.id)}
                    onChange={() => selection.onToggleRow(ticket.id)}
                    onClick={(e) => e.stopPropagation()}
                    data-testid={`queue-row-check-${ticket.id}`}
                    aria-label={t("queue.bulk.selectRow")}
                    className="rounded border-hairline text-accent focus:ring-accent"
                  />
                </span>
              )}
              <span className="hidden truncate font-mono text-xs text-accent md:inline">
                {ticket.tn}
                {escalationBadge}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-[12.8px] font-medium text-ink" title={ticket.title ?? ""}>
                  {ticket.title || "—"}
                </span>
                <span className="block truncate text-[11.5px] text-muted">
                  {ticket.customer_user_id || ticket.customer_id || "—"}
                </span>
              </span>
              <span className="hidden min-w-0 items-center md:inline-flex">
                <StateChip
                  state={ticket.state}
                  empty="—"
                  className="max-w-full truncate"
                  data-testid={`ticket-state-chip-${ticket.id}`}
                />
              </span>
              <span className="hidden min-w-0 items-center md:inline-flex">
                <PriorityChip
                  priority={ticket.priority}
                  priorityId={ticket.priority_id}
                  empty="—"
                  className="max-w-full truncate"
                  data-testid={`ticket-priority-chip-${ticket.id}`}
                />
              </span>
              <span className="hidden truncate text-xs text-muted md:inline">
                {ticket.owner_name || ticket.owner_login || "—"}
              </span>
              <span
                className="hidden truncate text-right font-mono text-[11.5px] tabular-nums text-muted md:inline"
                title={formatDateTime(ticket.change_time, locale)}
              >
                {formatAgeSeconds(ticket.age_seconds, locale)}
              </span>

              {/* Mobile card footer: state + priority chips + owner */}
              <div className="flex items-center justify-between gap-2 text-[11.5px] text-muted md:hidden">
                <span className="flex min-w-0 items-center gap-1.5 truncate">
                  <StateChip
                    state={ticket.state}
                    data-testid={`ticket-state-chip-mobile-${ticket.id}`}
                  />
                  <PriorityChip
                    priority={ticket.priority}
                    priorityId={ticket.priority_id}
                    data-testid={`ticket-priority-chip-mobile-${ticket.id}`}
                  />
                </span>
                <span className="shrink-0 truncate">
                  {ticket.owner_name || ticket.owner_login || "—"}
                </span>
              </div>
              {esc && <span className="md:hidden">{escalationBadge}</span>}
            </div>
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
