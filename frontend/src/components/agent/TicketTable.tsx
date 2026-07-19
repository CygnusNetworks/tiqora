import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "@tanstack/react-router";
import type { TicketListItem } from "@/lib/api";
import { formatAgeSeconds, formatDateTime, isEscalated } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
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
};

const SORT_COLUMNS: { key: SortKey; labelKey: string }[] = [
  { key: "tn", labelKey: "ticket.tn" },
  { key: "title", labelKey: "ticket.title" },
  { key: "state", labelKey: "ticket.state" },
  { key: "priority", labelKey: "ticket.priority" },
  { key: "owner", labelKey: "ticket.owner" },
  { key: "customer", labelKey: "ticket.customer" },
  { key: "age", labelKey: "ticket.age" },
  { key: "changed", labelKey: "ticket.changed" },
];

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
}: TicketTableProps) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [focusIdx, setFocusIdx] = useState(0);
  const tableRef = useRef<HTMLTableElement>(null);
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  useEffect(() => {
    setFocusIdx(0);
  }, [items]);

  useEffect(() => {
    const el = tableRef.current;
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
      } else if (e.key === "Enter" && items[focusIdx]) {
        e.preventDefault();
        void navigate({
          to: "/agent/tickets/$ticketId",
          params: { ticketId: String(items[focusIdx].id) },
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, focusIdx, navigate]);

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
    <div className="flex flex-col gap-2" data-testid="ticket-table">
      <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
        <table ref={tableRef} className="w-full min-w-[720px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-hairline bg-surface-subtle text-xs uppercase tracking-wide text-muted">
              {SORT_COLUMNS.map((col) => (
                <th key={col.key} className="py-1.5 pl-4 pr-2 font-medium">
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                    onClick={() => toggleSort(col.key)}
                  >
                    {t(col.labelKey)}
                    {sort === col.key && (
                      <span aria-hidden>{order === "asc" ? "↑" : "↓"}</span>
                    )}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading && items.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-8 text-center text-muted">
                  <Spinner className="mx-auto" />
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-8 text-center text-muted">
                  {t("ticket.noTickets")}
                </td>
              </tr>
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
              const spineColor =
                escLevel === "none" ? stateColorVar(ticket.state) : undefined;
              return (
                <tr
                  key={ticket.id}
                  data-testid={`ticket-row-${ticket.id}`}
                  className={cn(
                    "h-10 cursor-pointer border-b border-hairline transition-colors duration-100 hover:bg-surface-subtle",
                    spineClassName(escLevel),
                    idx === focusIdx &&
                      "bg-surface-subtle ring-1 ring-inset ring-accent/40",
                  )}
                  style={{ "--spine-color": spineColor } as CSSProperties}
                  onClick={() =>
                    void navigate({
                      to: "/agent/tickets/$ticketId",
                      params: { ticketId: String(ticket.id) },
                    })
                  }
                  onMouseEnter={() => setFocusIdx(idx)}
                >
                  <td className="py-1 pl-4 pr-2 font-mono text-xs text-accent">
                    {ticket.tn}
                    {esc && (
                      <span
                        className="ml-1.5 rounded bg-escalation/15 px-1 font-mono text-[10px] tabular-nums text-escalation"
                        title={t("ticket.escalated")}
                      >
                        {formatCountdown(
                          ticket.escalation_time ??
                            ticket.escalation_response_time ??
                            ticket.escalation_update_time ??
                            ticket.escalation_solution_time,
                        )}
                      </span>
                    )}
                  </td>
                  <td className="max-w-[16rem] truncate py-1 pr-2" title={ticket.title ?? ""}>
                    {ticket.title || "—"}
                  </td>
                  <td className="py-1 pr-2 text-xs">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        aria-hidden
                        className="h-1.5 w-1.5 rounded-full"
                        style={{ background: stateColorVar(ticket.state) }}
                      />
                      {ticket.state ?? "—"}
                    </span>
                  </td>
                  <td className="py-1 pr-2 text-xs">{ticket.priority ?? "—"}</td>
                  <td className="py-1 pr-2 text-xs">
                    {ticket.owner_name || ticket.owner_login || "—"}
                  </td>
                  <td className="py-1 pr-2 text-xs">
                    {ticket.customer_user_id || ticket.customer_id || "—"}
                  </td>
                  <td className="py-1 pr-2 font-mono text-xs tabular-nums text-muted">
                    {formatAgeSeconds(ticket.age_seconds, locale)}
                  </td>
                  <td className="py-1 pr-2 font-mono text-xs tabular-nums text-muted">
                    {formatDateTime(ticket.change_time, locale)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
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
