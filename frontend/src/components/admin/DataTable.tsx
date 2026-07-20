import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";

export type DataTableColumn<T> = {
  key: string;
  header: string;
  /** Renders as a monospace id-style column when true. */
  mono?: boolean;
  render: (row: T) => ReactNode;
  className?: string;
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
  /** True for a row whose valid_id !== 1 (or equivalent) — renders the invalid Badge. */
  isRowValid?: (row: T) => boolean;
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
  isRowValid,
  testId = "admin-data-table",
}: DataTableProps<T>) {
  const { t } = useTranslation();
  const hasActions = Boolean(onEdit || onDeactivate || onActivate);
  const colCount = columns.length + (hasActions ? 1 : 0) + (isRowValid ? 1 : 0);

  return (
    <div
      className="overflow-x-auto rounded-lg border border-hairline bg-surface"
      data-testid={testId}
    >
      <table className="w-full min-w-[640px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-hairline bg-surface-subtle text-xs uppercase tracking-wide text-muted">
            {columns.map((col) => (
              <th key={col.key} className={cn("py-1.5 pl-4 pr-2 font-medium", col.className)}>
                {col.header}
              </th>
            ))}
            {isRowValid && <th className="py-1.5 pr-2 font-medium">{t("admin.table.status")}</th>}
            {hasActions && (
              <th className="py-1.5 pr-4 text-right font-medium">{t("admin.table.actions")}</th>
            )}
          </tr>
        </thead>
        <tbody>
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
            return (
              <tr
                key={rowKey(row)}
                data-testid={`admin-row-${rowKey(row)}`}
                className="h-10 border-b border-hairline transition-colors duration-100 hover:bg-surface-subtle last:border-b-0"
              >
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
                          data-testid={`admin-row-edit-${rowKey(row)}`}
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
                          data-testid={`admin-row-deactivate-${rowKey(row)}`}
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
                          data-testid={`admin-row-activate-${rowKey(row)}`}
                          onClick={() => onActivate(row)}
                        >
                          <span className="inline-flex items-center gap-1">
                            <ActivateIcon />
                            {t("admin.table.activate")}
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
