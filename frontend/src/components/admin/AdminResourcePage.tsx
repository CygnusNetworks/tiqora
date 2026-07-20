import { useState } from "react";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError, type AdminListParams, type AdminPage, type AdminValidFilter } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { DataTable, type DataTableColumn } from "./DataTable";
import { CrudDrawer, type FieldDef, type FieldValues } from "./CrudDrawer";

export type AdminCrudApi<Out, Create, Update> = {
  list: (params?: AdminListParams, signal?: AbortSignal) => Promise<AdminPage<Out>>;
  create: (body: Create, signal?: AbortSignal) => Promise<Out>;
  update: (id: number | string, body: Update, signal?: AbortSignal) => Promise<Out>;
  deactivate: (id: number | string, signal?: AbortSignal) => Promise<void>;
};

export type AdminResourcePageProps<Out, Create, Update> = {
  resourceKey: string;
  title: string;
  newLabel: string;
  api: AdminCrudApi<Out, Create, Update>;
  idOf: (row: Out) => string | number;
  columns: DataTableColumn<Out>[];
  fields: FieldDef[];
  toFormValues: (row: Out | null) => FieldValues;
  toCreateBody: (values: FieldValues) => Create;
  toUpdateBody: (values: FieldValues) => Update;
  isRowValid?: (row: Out) => boolean;
  /** Default page size (rows per page). */
  pageSize?: number;
};

const defaultIsRowValid = (row: unknown): boolean =>
  (row as { valid_id?: number }).valid_id === undefined ||
  (row as { valid_id?: number }).valid_id === 1;

const VALID_FILTERS: AdminValidFilter[] = ["valid", "invalid", "all"];

/**
 * Generic list + create/edit drawer + deactivate flow, instantiated per
 * admin resource. Adds server-side pagination and a valid/invalid filter
 * (defaulting to hiding soft-deleted rows) so every admin list scales to
 * large master-data tables. Keeps each resource page down to column defs +
 * field defs.
 */
export function AdminResourcePage<Out, Create, Update>({
  resourceKey,
  title,
  newLabel,
  api,
  idOf,
  columns,
  fields,
  toFormValues,
  toCreateBody,
  toUpdateBody,
  isRowValid,
  pageSize: initialPageSize = 25,
}: AdminResourcePageProps<Out, Create, Update>) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<Out | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(initialPageSize);
  const [valid, setValid] = useState<AdminValidFilter>("valid");

  const listQ = useQuery({
    queryKey: ["admin", resourceKey, { page, pageSize, valid }],
    queryFn: ({ signal }) => api.list({ page, pageSize, valid }, signal),
    placeholderData: keepPreviousData,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin", resourceKey] });

  const rows = listQ.data?.items ?? [];
  const total = listQ.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const firstRow = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const lastRow = Math.min(page * pageSize, total);

  const createM = useMutation({
    mutationFn: (values: FieldValues) => api.create(toCreateBody(values)),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const updateM = useMutation({
    mutationFn: ({ id, values }: { id: string | number; values: FieldValues }) =>
      api.update(id, toUpdateBody(values)),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const deactivateM = useMutation({
    mutationFn: (id: string | number) => api.deactivate(id),
    onSuccess: () => invalidate(),
  });

  // Reactivation is a plain validity flip, reusing the resource's PATCH.
  const activateM = useMutation({
    mutationFn: (id: string | number) => api.update(id, { valid_id: 1 } as unknown as Update),
    onSuccess: () => invalidate(),
  });

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    setDrawerOpen(true);
  };

  const openEdit = (row: Out) => {
    setEditing(row);
    setFormError(null);
    setDrawerOpen(true);
  };

  const changeValid = (next: AdminValidFilter) => {
    setValid(next);
    setPage(1);
  };

  const changePageSize = (next: number) => {
    setPageSize(next);
    setPage(1);
  };

  const handleSubmit = async (values: FieldValues) => {
    setFormError(null);
    try {
      if (editing) {
        await updateM.mutateAsync({ id: idOf(editing), values });
      } else {
        await createM.mutateAsync(values);
      }
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
      throw err;
    }
  };

  return (
    <div className="space-y-3 p-4" data-testid={`admin-${resourceKey}-page`}>
      <div className="flex items-center justify-between gap-3">
        <h1 className="font-display text-xl font-semibold text-ink">{title}</h1>
        <Button variant="primary" size="sm" data-testid="admin-new-button" onClick={openCreate}>
          {newLabel}
        </Button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div
          className="inline-flex rounded-lg border border-hairline bg-surface p-0.5"
          role="group"
          aria-label={t("admin.filter.label")}
          data-testid={`admin-${resourceKey}-valid-filter`}
        >
          {VALID_FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={valid === f}
              data-testid={`admin-valid-${f}`}
              onClick={() => changeValid(f)}
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium transition-colors",
                valid === f
                  ? "bg-accent text-white"
                  : "text-muted hover:bg-surface-subtle hover:text-ink",
              )}
            >
              {t(`admin.filter.${f}`)}
            </button>
          ))}
        </div>
        <label className="inline-flex items-center gap-1.5 text-xs text-muted">
          {t("admin.pagination.pageSize")}
          <select
            className="rounded-md border border-hairline bg-surface px-1.5 py-1 text-xs text-ink"
            value={pageSize}
            data-testid={`admin-${resourceKey}-page-size`}
            onChange={(e) => changePageSize(Number(e.target.value))}
          >
            {[25, 50, 100, 200].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={idOf}
        isLoading={listQ.isLoading}
        isRowValid={isRowValid ?? defaultIsRowValid}
        onEdit={openEdit}
        onDeactivate={(row) => deactivateM.mutate(idOf(row))}
        onActivate={(row) => activateM.mutate(idOf(row))}
        testId={`admin-${resourceKey}-table`}
      />

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <span data-testid={`admin-${resourceKey}-count`}>
          {t("admin.pagination.showing", { from: firstRow, to: lastRow, total })}
        </span>
        <div className="inline-flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={page <= 1}
            data-testid="admin-page-prev"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            {t("admin.pagination.prev")}
          </Button>
          <span data-testid="admin-page-indicator">
            {t("admin.pagination.page", { page, total: totalPages })}
          </span>
          <Button
            size="sm"
            variant="secondary"
            disabled={page >= totalPages}
            data-testid="admin-page-next"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            {t("admin.pagination.next")}
          </Button>
        </div>
      </div>

      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={editing ? t("admin.form.editTitle", { title }) : newLabel}
        fields={fields}
        mode={editing ? "edit" : "create"}
        initialValues={toFormValues(editing)}
        onSubmit={handleSubmit}
        submitError={formError}
        testIdPrefix="admin-form"
      />
    </div>
  );
}
