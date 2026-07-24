import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";

export type DataTableSortOrder = "asc" | "desc";

export type DataTableSortState = {
  sort: string | null;
  order: DataTableSortOrder;
};

export type DataTableColumn<T> = {
  key: string;
  header: string;
  /** Renders as a monospace id-style column when true. */
  mono?: boolean;
  render: (row: T) => ReactNode;
  className?: string;
  /**
   * Opt-in: make this header clickable for server-side sort.
   * Only active when the table also receives `sort` + `onSortChange`.
   */
  sortable?: boolean;
  /**
   * API sort key when it differs from `key` (e.g. column key "changed" →
   * sortKey "change_time"). Defaults to `key`.
   */
  sortKey?: string;
};

export type DataTableSelection = {
  selected: Set<string | number>;
  /** ``range`` (Shift held) extends the selection from the last-toggled row. */
  onToggle: (id: string | number, range?: boolean) => void;
  onToggleAll: () => void;
  allSelected: boolean;
  someSelected: boolean;
};

export type DataTableProps<T> = {
  columns: DataTableColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  isLoading?: boolean;
  emptyLabel?: string;
  onEdit?: (row: T) => void;
  onDeactivate?: (row: T) => void;
  /** Reactivate a soft-deleted row (valid_id → 1). Shown only for invalid rows. */
  onActivate?: (row: T) => void;
  /** Permanently remove a row (hard delete, unlike onDeactivate's soft revoke). Shown for every row. */
  onDelete?: (row: T) => void;
  /** True for a row whose valid_id !== 1 (or equivalent) — renders the invalid Badge. */
  isRowValid?: (row: T) => boolean;
  /** Opt-in leading checkbox column for bulk selection. */
  selection?: DataTableSelection;
  /**
   * Opt-in sort state. When omitted (with `onSortChange`), headers render
   * exactly as static text — other admin pages stay unchanged.
   */
  sort?: DataTableSortState | null;
  /** Called with the next sort state (null sort = clear back to server default). */
  onSortChange?: (next: DataTableSortState) => void;
  /**
   * When true and sort props are present, the status column header is sortable
   * with API key `valid_id`.
   */
  statusSortable?: boolean;
  /**
   * True while a background refetch (not the initial load) is in flight.
   * Dims the row body and blocks pointer interaction there, leaving the
   * header/toolbar interactive — old rows stay visible underneath.
   */
  busy?: boolean;
  testId?: string;
};

function EditIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
      <path
        d="M4 20h4l10.5-10.5a2.12 2.12 0 0 0-3-3L5 17v3Z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DeactivateIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="1.8" />
      <path d="m6 6 12 12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function ActivateIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
      <path
        d="m5 12.5 4.5 4.5L19 7"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DeleteIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
      <path
        d="M5 7h14M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7m2 0-.7 12.1a2 2 0 0 1-2 1.9H9.7a2 2 0 0 1-2-1.9L7 7"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function cycleSort(
  current: DataTableSortState | null | undefined,
  sortKey: string,
): DataTableSortState {
  if (!current || current.sort !== sortKey) {
    return { sort: sortKey, order: "asc" };
  }
  if (current.order === "asc") {
    return { sort: sortKey, order: "desc" };
  }
  // desc → clear (server default)
  return { sort: null, order: "asc" };
}

function SortableHeader({
  label,
  sortKey,
  sort,
  onSortChange,
  className,
  testId,
}: {
  label: string;
  sortKey: string;
  sort: DataTableSortState | null | undefined;
  onSortChange: (next: DataTableSortState) => void;
  className?: string;
  testId?: string;
}) {
  const active = sort?.sort === sortKey;
  const ariaSort = !active ? "none" : sort?.order === "asc" ? "ascending" : "descending";
  const indicator = !active ? "" : sort?.order === "asc" ? " ▲" : " ▼";

  return (
    <th
      className={cn("py-1.5 pl-4 pr-2 font-medium", className)}
      aria-sort={ariaSort}
    >
      <button
        type="button"
        className="inline-flex items-center gap-0.5 rounded-sm uppercase tracking-wide text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        onClick={() => onSortChange(cycleSort(sort, sortKey))}
        data-testid={testId ?? `admin-sort-${sortKey}`}
      >
        <span>{label}</span>
        <span className="inline-block w-3 text-[10px] tabular-nums" aria-hidden="true">
          {indicator.trim()}
        </span>
      </button>
    </th>
  );
}

/**
 * Generic dense admin data table: mono id-style rendering per column,
 * valid/invalid Badge, horizontal scroll contained to the table wrapper
 * (never the page), row-level edit/deactivate actions.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  isLoading,
  emptyLabel,
  onEdit,
  onDeactivate,
  onActivate,
  onDelete,
  isRowValid,
  selection,
  sort,
  onSortChange,
  statusSortable = false,
  busy = false,
  testId = "admin-data-table",
}: DataTableProps<T>) {
  const { t } = useTranslation();
  const hasActions = Boolean(onEdit || onDeactivate || onActivate || onDelete);
  const hasSelection = Boolean(selection);
  const sortEnabled = Boolean(onSortChange);
  const colCount =
    columns.length + (hasActions ? 1 : 0) + (isRowValid ? 1 : 0) + (hasSelection ? 1 : 0);

  return (
    <div
      className="overflow-x-auto rounded-lg border border-hairline bg-surface"
      data-testid={testId}
    >
      <table className="w-full min-w-[640px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-hairline bg-surface-subtle text-xs uppercase tracking-wide text-muted">
            {hasSelection && selection && (
              <th className="w-10 py-1.5 pl-3 pr-1 font-medium">
                <input
                  type="checkbox"
                  checked={selection.allSelected}
                  ref={(el) => {
                    if (el) el.indeterminate = selection.someSelected;
                  }}
                  onChange={selection.onToggleAll}
                  data-testid="admin-select-all"
                  aria-label={t("admin.bulk.selectAll")}
                  className="rounded border-hairline text-accent focus:ring-accent"
                />
              </th>
            )}
            {columns.map((col) => {
              const colSortKey = col.sortKey ?? col.key;
              if (sortEnabled && col.sortable && onSortChange) {
                return (
                  <SortableHeader
                    key={col.key}
                    label={col.header}
                    sortKey={colSortKey}
                    sort={sort}
                    onSortChange={onSortChange}
                    className={col.className}
                  />
                );
              }
              return (
                <th key={col.key} className={cn("py-1.5 pl-4 pr-2 font-medium", col.className)}>
                  {col.header}
                </th>
              );
            })}
            {isRowValid &&
              (sortEnabled && statusSortable && onSortChange ? (
                <SortableHeader
                  label={t("admin.table.status")}
                  sortKey="valid_id"
                  sort={sort}
                  onSortChange={onSortChange}
                  className="pr-2 pl-0"
                  testId="admin-sort-valid_id"
                />
              ) : (
                <th className="py-1.5 pr-2 font-medium">{t("admin.table.status")}</th>
              ))}
            {hasActions && (
              <th className="py-1.5 pr-4 text-right font-medium">{t("admin.table.actions")}</th>
            )}
          </tr>
        </thead>
        <tbody
          data-state={busy ? "busy" : undefined}
          className={cn(busy && "pointer-events-none opacity-45 transition-opacity duration-150")}
        >
          {isLoading && rows.length === 0 && (
            <tr>
              <td colSpan={colCount} className="px-3 py-8 text-center text-muted">
                <Spinner className="mx-auto" />
              </td>
            </tr>
          )}
          {!isLoading && rows.length === 0 && (
            <tr>
              <td colSpan={colCount} className="px-3 py-8 text-center text-muted">
                {emptyLabel ?? t("admin.table.empty")}
              </td>
            </tr>
          )}
          {rows.map((row) => {
            const valid = isRowValid?.(row) ?? true;
            const id = rowKey(row);
            return (
              <tr
                key={id}
                data-testid={`admin-row-${id}`}
                className="h-10 border-b border-hairline transition-colors duration-100 hover:bg-surface-subtle last:border-b-0"
              >
                {hasSelection && selection && (
                  <td className="py-1 pl-3 pr-1">
                    <input
                      type="checkbox"
                      checked={selection.selected.has(id)}
                      // onClick carries shiftKey (onChange does not) — needed
                      // for Shift-click range selection; onChange stays a no-op
                      // so the controlled checkbox doesn't warn.
                      onClick={(e) => selection.onToggle(id, e.shiftKey)}
                      onChange={() => {}}
                      data-testid={`admin-row-select-${id}`}
                      aria-label={t("admin.bulk.selectRow")}
                      className="rounded border-hairline text-accent focus:ring-accent"
                    />
                  </td>
                )}
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={cn(
                      "py-1 pl-4 pr-2 text-xs",
                      col.mono && "font-mono text-muted",
                      col.className,
                    )}
                  >
                    {col.render(row)}
                  </td>
                ))}
                {isRowValid && (
                  <td className="py-1 pr-2">
                    <Badge tone={valid ? "success" : "muted"}>
                      {valid ? t("admin.table.valid") : t("admin.table.invalid")}
                    </Badge>
                  </td>
                )}
                {hasActions && (
                  <td className="py-1 pr-4 text-right">
                    <div className="inline-flex items-center gap-1.5">
                      {onEdit && (
                        <Button
                          size="sm"
                          variant="secondary"
                          data-testid={`admin-row-edit-${id}`}
                          onClick={() => onEdit(row)}
                        >
                          <span className="inline-flex items-center gap-1">
                            <EditIcon />
                            {t("admin.table.edit")}
                          </span>
                        </Button>
                      )}
                      {onDeactivate && valid && (
                        <Button
                          size="sm"
                          variant="danger"
                          data-testid={`admin-row-deactivate-${id}`}
                          onClick={() => onDeactivate(row)}
                        >
                          <span className="inline-flex items-center gap-1">
                            <DeactivateIcon />
                            {t("admin.table.deactivate")}
                          </span>
                        </Button>
                      )}
                      {onActivate && !valid && (
                        <Button
                          size="sm"
                          variant="secondary"
                          data-testid={`admin-row-activate-${id}`}
                          onClick={() => onActivate(row)}
                        >
                          <span className="inline-flex items-center gap-1">
                            <ActivateIcon />
                            {t("admin.table.activate")}
                          </span>
                        </Button>
                      )}
                      {onDelete && (
                        <Button
                          size="sm"
                          variant="danger"
                          data-testid={`admin-row-delete-${id}`}
                          onClick={() => onDelete(row)}
                        >
                          <span className="inline-flex items-center gap-1">
                            <DeleteIcon />
                            {t("admin.table.delete")}
                          </span>
                        </Button>
                      )}
                    </div>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
