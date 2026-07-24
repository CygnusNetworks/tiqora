import { useEffect, useMemo, useRef, useState } from "react";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError, type AdminListParams, type AdminPage, type AdminValidFilter } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { CheckIcon, PlusIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import {
  DataTable,
  type DataTableColumn,
  type DataTableSortOrder,
  type DataTableSortState,
} from "./DataTable";
import { CrudDrawer, type FieldDef, type FieldValues } from "./CrudDrawer";

export type AdminCrudApi<Out, Create, Update> = {
  list: (params?: AdminListParams, signal?: AbortSignal) => Promise<AdminPage<Out>>;
  create: (body: Create, signal?: AbortSignal) => Promise<Out>;
  update: (id: number | string, body: Update, signal?: AbortSignal) => Promise<Out>;
  deactivate: (id: number | string, signal?: AbortSignal) => Promise<void>;
};

export type AdminBulkActionContext = {
  /** Chunked actions call this after each chunk so the bar can show progress. */
  onProgress?: (done: number, total: number) => void;
};

export type AdminBulkAction = {
  key: string;
  label: string;
  /** May return `{ updated }` so the bar can report an exact count. */
  run: (
    ids: Array<number | string>,
    ctx: AdminBulkActionContext,
  ) => Promise<void | { updated: number }>;
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
  /**
   * Opt-in server-side search. When true, renders a debounced search input and
   * forwards `search` to `api.list`. Default off — other admin pages unchanged.
   */
  searchable?: boolean;
  /**
   * Opt-in bulk selection + floating action bar. When provided, adds a leading
   * checkbox column and a bottom pill bar. Default off.
   */
  bulkActions?: AdminBulkAction[];
  /**
   * Opt-in "Alle" (all rows) page-size option. Uses `allPageSize` as a UI
   * sentinel only; the client fetches the table in 500-row chunks instead of
   * one mega-request. Default off.
   */
  allowAllPageSize?: boolean;
  /**
   * Sentinel value for the "Alle" select option (default 100_000). Not sent to
   * the backend as a real page_size — see `fetchAllChunked`.
   */
  allPageSize?: number;
  /**
   * Opt-in server-side column sorting. When true, sortable column headers
   * forward `sort`/`order` to `api.list`. Columns still need `sortable: true`.
   * Default off — other admin pages unchanged.
   */
  sortable?: boolean;
  /**
   * When `sortable` is on, also make the status column header sort by
   * `valid_id`. Default off.
   */
  statusSortable?: boolean;
};

const defaultIsRowValid = (row: unknown): boolean =>
  (row as { valid_id?: number }).valid_id === undefined ||
  (row as { valid_id?: number }).valid_id === 1;

const VALID_FILTERS: AdminValidFilter[] = ["valid", "invalid", "all"];

const PAGE_SIZE_OPTIONS = [25, 50, 100, 200, 500] as const;
/**
 * Sentinel for the "Alle" UI option — not a real backend page_size. When
 * selected, the client loads the table via `fetchAllChunked` (500-row pages)
 * so a reverse proxy cannot kill one 100k-row response.
 */
const DEFAULT_ALL_PAGE_SIZE = 100_000;
/** Chunk size for "Alle" client-side concatenation. */
const ALL_CHUNK_SIZE = 500;
/** Hard cap on chunk requests (~200k rows) to prevent infinite loops. */
const ALL_CHUNK_HARD_MAX = 400;
/** Delay before showing the "refreshing" busy treatment, to avoid flicker on fast refetches. */
const REFRESH_BUSY_DELAY_MS = 200;
/** How long the post-refetch success tone stays on the count chip. */
const REFRESH_SUCCESS_DURATION_MS = 3000;

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}

/**
 * Fetch every row of an admin list by walking bounded `chunk`-sized pages and
 * concatenating. Used when the "Alle" page-size option is active so we never
 * issue a single proxy-killing mega-request.
 *
 * Returns a synthetic `AdminPage` with `page: 1` and `page_size: items.length`
 * so the existing pagination UI (totalPages collapses to 1) keeps working.
 */
async function fetchAllChunked<Out>(
  list: (params?: AdminListParams, signal?: AbortSignal) => Promise<AdminPage<Out>>,
  baseParams: Omit<AdminListParams, "page" | "pageSize">,
  signal?: AbortSignal,
  chunk: number = ALL_CHUNK_SIZE,
): Promise<AdminPage<Out>> {
  const first = await list({ ...baseParams, page: 1, pageSize: chunk }, signal);
  const total = first.total;
  const items: Out[] = [...first.items];

  if (items.length === 0 || items.length >= total) {
    return { items, total, page: 1, page_size: items.length };
  }

  const maxPages = Math.min(Math.ceil(total / chunk), ALL_CHUNK_HARD_MAX);
  for (let page = 2; page <= maxPages; page++) {
    if (signal?.aborted) {
      throw new DOMException("The operation was aborted.", "AbortError");
    }
    const next = await list({ ...baseParams, page, pageSize: chunk }, signal);
    if (next.items.length === 0) break;
    items.push(...next.items);
    if (items.length >= total) break;
  }

  return { items, total, page: 1, page_size: items.length };
}

/**
 * Generic list + create/edit drawer + deactivate flow, instantiated per
 * admin resource. Adds server-side pagination and a valid/invalid filter
 * (defaulting to hiding soft-deleted rows) so every admin list scales to
 * large master-data tables. Keeps each resource page down to column defs +
 * field defs.
 *
 * Optional enhancements (all default off so existing pages stay unchanged):
 * - `searchable` — debounced server-side search input
 * - `bulkActions` — row checkboxes + floating bulk action bar
 * - page-size select includes 500 (backend ListParams max)
 * - `allowAllPageSize` — "Alle" option that chunk-fetches all rows (500 each)
 * - `sortable` — server-side column header sorting (`sort`/`order`)
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
  searchable = false,
  bulkActions,
  allowAllPageSize = false,
  allPageSize = DEFAULT_ALL_PAGE_SIZE,
  sortable = false,
  statusSortable = false,
}: AdminResourcePageProps<Out, Create, Update>) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<Out | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(initialPageSize);
  const [valid, setValid] = useState<AdminValidFilter>("valid");
  const [searchInput, setSearchInput] = useState("");
  const debouncedSearch = useDebouncedValue(searchInput, 300);
  const search = searchable ? debouncedSearch.trim() : "";
  const [sortState, setSortState] = useState<DataTableSortState>({ sort: null, order: "asc" });
  const sort = sortable ? sortState.sort : undefined;
  const order: DataTableSortOrder | undefined =
    sortable && sortState.sort ? sortState.order : undefined;

  const [selected, setSelected] = useState<Set<string | number>>(() => new Set());
  // Anchor for Shift-click range selection: the last row toggled on its own.
  const rangeAnchorRef = useRef<string | number | null>(null);
  const bulkEnabled = Boolean(bulkActions && bulkActions.length > 0);
  // Key of the currently-running bulk action, or null when idle.
  const [bulkBusy, setBulkBusy] = useState<string | null>(null);
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null);
  const [bulkStatus, setBulkStatus] = useState<{ tone: "success" | "error"; text: string } | null>(
    null,
  );

  // Success feedback auto-dismisses; errors stay until the next action.
  useEffect(() => {
    if (bulkStatus?.tone !== "success") return;
    const handle = window.setTimeout(() => setBulkStatus(null), 6000);
    return () => window.clearTimeout(handle);
  }, [bulkStatus]);

  // Reset to page 1 when the debounced search term changes.
  useEffect(() => {
    if (!searchable) return;
    setPage(1);
  }, [search, searchable]);

  // Drop selection when the list context changes (filter / search / page size / sort).
  useEffect(() => {
    setSelected(new Set());
    rangeAnchorRef.current = null;
  }, [valid, search, pageSize, sort, order]);

  const listQ = useQuery({
    queryKey: [
      "admin",
      resourceKey,
      {
        page,
        pageSize,
        valid,
        search: search || undefined,
        sort: sort || undefined,
        order: order || undefined,
      },
    ],
    queryFn: ({ signal }) => {
      const base = {
        valid,
        ...(search ? { search } : {}),
        ...(sort ? { sort, order } : {}),
      };
      // "Alle" is a UI sentinel only — never send allPageSize to the backend.
      if (allowAllPageSize && pageSize === allPageSize) {
        return fetchAllChunked(api.list, base, signal);
      }
      return api.list(
        {
          page,
          pageSize,
          ...base,
        },
        signal,
      );
    },
    placeholderData: keepPreviousData,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin", resourceKey] });

  const rows = useMemo(() => listQ.data?.items ?? [], [listQ.data?.items]);
  const total = listQ.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const firstRow = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const lastRow = Math.min(page * pageSize, total);

  // Background refetch, as opposed to the initial load (which keeps the
  // existing full-table loading state in DataTable).
  const isRefetching = listQ.isFetching && !listQ.isLoading;

  // Delay-guarded so a refetch that resolves within ~200ms never flickers.
  const [refreshBusy, setRefreshBusy] = useState(false);
  useEffect(() => {
    if (!isRefetching) {
      setRefreshBusy(false);
      return;
    }
    const handle = window.setTimeout(() => setRefreshBusy(true), REFRESH_BUSY_DELAY_MS);
    return () => window.clearTimeout(handle);
  }, [isRefetching]);

  // Green "success" tone on the count chip for a few seconds after a refetch
  // completes, then back to neutral.
  const [refreshSuccess, setRefreshSuccess] = useState(false);
  const wasRefetchingRef = useRef(false);
  useEffect(() => {
    if (isRefetching) {
      wasRefetchingRef.current = true;
      setRefreshSuccess(false);
      return;
    }
    if (!wasRefetchingRef.current) return;
    wasRefetchingRef.current = false;
    setRefreshSuccess(true);
    const handle = window.setTimeout(() => setRefreshSuccess(false), REFRESH_SUCCESS_DURATION_MS);
    return () => window.clearTimeout(handle);
  }, [isRefetching]);

  const allLoaded = allowAllPageSize && pageSize === allPageSize;
  const countText =
    total === 0
      ? t("admin.list.countNone")
      : allLoaded
        ? t("admin.list.countShownAll", { total })
        : t("admin.list.countShown", { total, from: firstRow, to: lastRow });

  const pageIds = useMemo(() => rows.map((r) => idOf(r)), [rows, idOf]);
  const allPageSelected =
    bulkEnabled && pageIds.length > 0 && pageIds.every((id) => selected.has(id));
  const somePageSelected =
    bulkEnabled && pageIds.some((id) => selected.has(id)) && !allPageSelected;

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

  const changeSort = (next: DataTableSortState) => {
    setSortState(next);
    setPage(1);
  };

  const toggleRow = (id: string | number, range = false) => {
    const targetIndex = pageIds.indexOf(id);
    const anchor = rangeAnchorRef.current;
    const anchorIndex = anchor != null ? pageIds.indexOf(anchor) : -1;

    // Shift-click with a valid anchor on the same page: select the whole
    // contiguous span between anchor and target (add, never toggle off —
    // matching the familiar file-list / spreadsheet behaviour).
    if (range && targetIndex !== -1 && anchorIndex !== -1) {
      const [lo, hi] =
        anchorIndex <= targetIndex ? [anchorIndex, targetIndex] : [targetIndex, anchorIndex];
      setSelected((prev) => {
        const next = new Set(prev);
        for (let i = lo; i <= hi; i += 1) next.add(pageIds[i]);
        return next;
      });
      rangeAnchorRef.current = id;
      return;
    }

    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    rangeAnchorRef.current = id;
  };

  const toggleAllOnPage = () => {
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

  const runBulkAction = async (action: AdminBulkAction) => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    setBulkBusy(action.key);
    setBulkStatus(null);
    setBulkProgress(null);
    try {
      const result = await action.run(ids, {
        onProgress: (done, total) => setBulkProgress({ done, total }),
      });
      const count = result && typeof result === "object" ? result.updated : ids.length;
      setBulkStatus({ tone: "success", text: t("admin.bulk.done", { count }) });
      setSelected(new Set());
      await invalidate();
    } catch (err) {
      const message =
        err instanceof ApiError && err.message && !err.message.startsWith("HTTP ")
          ? err.message
          : t("admin.bulk.errorGeneric");
      // Selection is intentionally kept so the user can retry.
      setBulkStatus({ tone: "error", text: t("admin.bulk.error", { message }) });
    } finally {
      setBulkBusy(null);
      setBulkProgress(null);
    }
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

  // Ensure the current pageSize is always a selectable option (e.g. custom default).
  // "Alle" is a separate labelled option (value = allPageSize), not a bare number.
  const pageSizeOptions = useMemo(() => {
    const base: number[] = [...PAGE_SIZE_OPTIONS];
    if (
      !base.includes(pageSize) &&
      !(allowAllPageSize && pageSize === allPageSize)
    ) {
      base.push(pageSize);
      base.sort((a, b) => a - b);
    }
    return base;
  }, [pageSize, allowAllPageSize, allPageSize]);

  return (
    <div className="space-y-3 p-4" data-testid={`admin-${resourceKey}-page`}>
      <div className="flex items-center justify-between gap-3">
        <h1 className="font-display text-xl font-semibold text-ink">{title}</h1>
        <Button
          variant="primary"
          size="sm"
          data-testid="admin-new-button"
          onClick={openCreate}
          aria-label={newLabel}
          title={newLabel}
          className="!px-2"
        >
          <PlusIcon className="text-[16px]" />
        </Button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
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
          {searchable && (
            <label className="inline-flex items-center gap-1.5 text-xs text-muted">
              <span className="sr-only">{t("admin.search")}</span>
              <input
                type="search"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder={t("admin.searchPlaceholder")}
                data-testid={`admin-${resourceKey}-search`}
                className="w-56 rounded-md border border-hairline bg-surface px-2 py-1 text-xs text-ink placeholder:text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </label>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span
            data-testid="admin-list-count"
            data-state={refreshBusy ? "busy" : refreshSuccess ? "success" : "neutral"}
            className={cn(
              "inline-flex items-center gap-1 rounded-full px-2 py-1 font-mono text-xs tabular-nums transition-colors",
              refreshBusy && "bg-accent-dim text-accent",
              !refreshBusy && refreshSuccess && "bg-green/15 text-green",
              !refreshBusy && !refreshSuccess && "text-muted",
            )}
          >
            {refreshBusy && <Spinner className="h-3 w-3" />}
            {!refreshBusy && refreshSuccess && <CheckIcon className="text-[13px]" />}
            {refreshBusy ? t("admin.list.refreshing") : countText}
          </span>
          <label className="inline-flex items-center gap-1.5 text-xs text-muted">
            {t("admin.pagination.pageSize")}
            <SelectField
              items={[
                ...pageSizeOptions.map((n) => ({ value: n, label: String(n) })),
                ...(allowAllPageSize
                  ? [{ value: allPageSize, label: t("admin.pagination.all") }]
                  : []),
              ]}
              value={pageSize}
              onChange={changePageSize}
              testId={`admin-${resourceKey}-page-size`}
              className="w-auto px-1.5 py-1 text-xs"
            />
          </label>
        </div>
      </div>

      {refreshBusy && (
        <div
          className="h-[3px] w-full overflow-hidden rounded-full bg-accent-dim"
          data-testid="admin-list-progress"
          role="progressbar"
          aria-label={t("admin.list.refreshing")}
        >
          <div className="admin-list-progress-bar h-full w-1/3 rounded-full bg-accent" />
        </div>
      )}

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={idOf}
        isLoading={listQ.isLoading}
        isRowValid={isRowValid ?? defaultIsRowValid}
        onEdit={openEdit}
        onDeactivate={(row) => deactivateM.mutate(idOf(row))}
        onActivate={(row) => activateM.mutate(idOf(row))}
        selection={
          bulkEnabled
            ? {
                selected,
                onToggle: toggleRow,
                onToggleAll: toggleAllOnPage,
                allSelected: allPageSelected,
                someSelected: somePageSelected,
              }
            : undefined
        }
        sort={sortable ? sortState : undefined}
        onSortChange={sortable ? changeSort : undefined}
        statusSortable={sortable && statusSortable}
        busy={refreshBusy}
        testId={`admin-${resourceKey}-table`}
      />

      <div className="flex flex-wrap items-center justify-end gap-2 text-xs text-muted">
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

      {bulkEnabled && (selected.size > 0 || bulkStatus !== null) && bulkActions && (
        <div
          className="fixed bottom-6 left-1/2 z-40 flex -translate-x-1/2 flex-wrap items-center gap-2 rounded-full border border-accent/50 bg-accent-dim px-3 py-2 shadow-lg"
          data-testid="admin-bulk-bar"
          role="toolbar"
          aria-label={t("admin.bulk.selected", { count: selected.size })}
        >
          {selected.size > 0 && (
            <span
              className="rounded-full bg-accent px-2 py-0.5 font-mono text-[11px] tabular-nums text-white"
              data-testid="admin-bulk-count"
            >
              {t("admin.bulk.selected", { count: selected.size })}
            </span>
          )}
          {selected.size > 0 &&
            bulkActions.map((action) => (
              <Button
                key={action.key}
                size="sm"
                variant="secondary"
                disabled={bulkBusy !== null}
                data-testid={`admin-bulk-action-${action.key}`}
                onClick={() => void runBulkAction(action)}
                className="border-accent/40 bg-surface text-accent hover:bg-surface-subtle"
              >
                {bulkBusy === action.key && <Spinner className="mr-1 h-3 w-3" />}
                {action.label}
              </Button>
            ))}
          {bulkBusy && bulkProgress && (
            <span
              className="font-mono text-[11px] tabular-nums text-accent"
              data-testid="admin-bulk-progress"
            >
              {t("admin.bulk.progress", { done: bulkProgress.done, total: bulkProgress.total })}
            </span>
          )}
          {bulkStatus && (
            <span
              className={cn(
                "text-xs font-medium",
                bulkStatus.tone === "success" ? "text-green" : "text-danger",
              )}
              data-testid="admin-bulk-status"
            >
              {bulkStatus.text}
            </span>
          )}
        </div>
      )}

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
