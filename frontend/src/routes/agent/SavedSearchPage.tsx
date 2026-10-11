import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { TicketTable } from "@/components/agent/TicketTable";
import { SAVED_SEARCHES_KEY } from "@/components/searches/savedSearchQueries";
import { toTableSortKey, toTicketSortKey } from "@/components/searches/searchSort";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { ApiError } from "@/lib/api";
import { reportErrorMessage } from "@/lib/reportsApi";
import { savedSearchesApi } from "@/lib/savedSearchesApi";
import type { SavedSearchSearch } from "./savedSearchSearch";

const PAGE_SIZE = 50;

function Notice({ testId, children }: { testId: string; children: React.ReactNode }) {
  return (
    <div className="m-6 rounded-lg border border-hairline bg-surface p-8 text-center text-sm text-ink" data-testid={testId}>
      {children}
    </div>
  );
}

/** `/agent/searches/$searchId`: the tickets matching a saved search, with sort and page in the address. */
export function SavedSearchPage() {
  const { t } = useTranslation();
  const navigate = useNavigate({ from: "/agent/searches/$searchId" });
  const queryClient = useQueryClient();
  const { searchId } = useParams({ strict: false }) as { searchId: string };
  const search = useSearch({ strict: false }) as SavedSearchSearch;
  const id = Number(searchId);
  const validId = Number.isInteger(id) && id > 0;
  const [confirmDelete, setConfirmDelete] = useState(false);

  const searchQ = useQuery({
    queryKey: [...SAVED_SEARCHES_KEY, "get", id],
    queryFn: ({ signal }) => savedSearchesApi.get(id, signal),
    enabled: validId,
    retry: false,
  });
  const saved = searchQ.data;
  const sort = search.sort ?? saved?.sort_by ?? "activity";
  const order = search.order ?? saved?.sort_order ?? "desc";
  const offset = search.offset ?? 0;
  const runnable = saved != null && saved.criteria != null && !saved.criteria_error;

  const ticketsQ = useQuery({
    queryKey: ["tickets", "saved-search", id, saved?.version, sort, order, offset],
    queryFn: ({ signal }) =>
      savedSearchesApi.queryTickets(
        {
          filter: saved!.criteria!,
          sort,
          order,
          offset,
          limit: PAGE_SIZE,
          include_archived: saved!.include_archived,
        },
        signal,
      ),
    enabled: runnable,
    retry: false,
  });

  const pin = useMutation({
    mutationFn: (on: boolean) =>
      savedSearchesApi.setPrefs(id, {
        show_in_sidebar: on,
        show_count: saved?.show_count ?? false,
      }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: SAVED_SEARCHES_KEY }),
  });
  const remove = useMutation({
    mutationFn: () => savedSearchesApi.remove(id),
    onSuccess: () => {
      setConfirmDelete(false);
      void queryClient.invalidateQueries({ queryKey: SAVED_SEARCHES_KEY });
      void navigate({ to: "/agent" });
    },
  });

  const setSearch = (patch: Partial<SavedSearchSearch>) =>
    void navigate({ search: (prev: SavedSearchSearch) => ({ ...prev, ...patch }), replace: true });

  if (!validId || (searchQ.error instanceof ApiError && searchQ.error.status === 404)) {
    return <Notice testId="saved-search-not-found">{t("savedSearches.notFound")}</Notice>;
  }
  if (searchQ.isError) {
    return (
      <Notice testId="saved-search-error">{reportErrorMessage(searchQ.error, t("savedSearches.loadError"))}</Notice>
    );
  }
  if (!saved) {
    return (
      <div className="p-8 text-center" role="status" aria-label={t("common.loading")}>
        <Spinner className="mx-auto" />
      </div>
    );
  }

  const queryBroken =
    ticketsQ.error instanceof ApiError && (ticketsQ.error.status === 422 || ticketsQ.error.status === 409);
  const broken = !runnable || queryBroken;
  const items = ticketsQ.data?.items ?? [];

  return (
    <div className="min-w-0 flex-1 space-y-3 p-3" data-testid="saved-search-page">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1 basis-64">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="font-display text-xl font-bold tracking-tight text-ink" data-testid="saved-search-title">
              {saved.name}
            </h1>
            {ticketsQ.data && (
              <span
                className="rounded-full bg-accent-dim px-2.5 py-0.5 font-mono text-[11px] tabular-nums text-accent"
                data-testid="saved-search-total"
              >
                {ticketsQ.data.total}
              </span>
            )}
          </div>
          {!saved.is_owner && (
            <p className="mt-0.5 text-[12.5px] text-muted" data-testid="saved-search-shared-by">
              {t("savedSearches.sharedBy", { name: saved.owner.name })}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-ink">
            <Switch
              checked={saved.show_in_sidebar}
              onChange={(on) => pin.mutate(on)}
              disabled={pin.isPending}
              testId="saved-search-pin"
            />
            {t("savedSearches.showInSidebar")}
          </label>
          {saved.can_edit && (
            <>
              <Link
                to="/agent/searches/$searchId/edit"
                params={{ searchId: String(saved.id) }}
                className="inline-flex items-center rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                data-testid="saved-search-edit"
              >
                {t("savedSearches.edit")}
              </Link>
              <Button className="text-danger" onClick={() => setConfirmDelete(true)} data-testid="saved-search-delete">
                {t("savedSearches.delete")}
              </Button>
            </>
          )}
        </div>
      </div>

      {broken ? (
        <Notice testId="saved-search-broken">
          <p>{t("savedSearches.broken")}</p>
          {saved.can_edit && (
            <Link
              to="/agent/searches/$searchId/edit"
              params={{ searchId: String(saved.id) }}
              className="mt-3 inline-flex items-center rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
              data-testid="saved-search-broken-edit"
            >
              {t("savedSearches.edit")}
            </Link>
          )}
        </Notice>
      ) : ticketsQ.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="saved-search-query-error">
          {reportErrorMessage(ticketsQ.error, t("savedSearches.loadError"))}
        </p>
      ) : (
        <TicketTable
          items={items}
          total={ticketsQ.data?.total ?? 0}
          offset={offset}
          limit={PAGE_SIZE}
          sort={toTableSortKey(sort)}
          order={order}
          isLoading={ticketsQ.isLoading}
          onSortChange={(s, o) => setSearch({ sort: toTicketSortKey(s), order: o, offset: undefined })}
          onPageChange={(off) => setSearch({ offset: off || undefined })}
          navContext={{
            label: saved.name,
            to: `/agent/searches/${saved.id}`,
            search: { offset: offset || undefined, sort: search.sort, order: search.order },
          }}
        />
      )}

      <ConfirmDialog
        open={confirmDelete}
        title={t("savedSearches.deleteTitle")}
        message={t("savedSearches.deleteConfirm", { name: saved.name })}
        confirmLabel={t("savedSearches.delete")}
        cancelLabel={t("savedSearches.cancel")}
        variant="danger"
        pending={remove.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
      />
      {remove.isError && (
        <p className="text-sm text-danger" role="alert">
          {reportErrorMessage(remove.error, t("savedSearches.deleteFailed"))}
        </p>
      )}
    </div>
  );
}
