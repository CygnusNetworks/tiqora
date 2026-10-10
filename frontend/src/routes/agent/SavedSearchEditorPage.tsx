import { useId, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { TicketTable } from "@/components/agent/TicketTable";
import { FilterBuilder } from "@/components/filters/FilterBuilder";
import { fieldsFromTicketCatalog } from "@/components/filters/filterFields";
import { ticketFilterErrors } from "@/components/filters/filterModel";
import { inputCls } from "@/components/reports/builder/builderUi";
import { SAVED_SEARCHES_KEY } from "@/components/searches/savedSearchQueries";
import { useDebouncedValue } from "@/components/searches/useDebouncedValue";
import { SearchShareFields } from "@/components/searches/SearchShareFields";
import { TICKET_SORT_KEYS, toTableSortKey, toTicketSortKey, type TicketSortKey } from "@/components/searches/searchSort";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { ApiError } from "@/lib/api";
import { reportErrorMessage } from "@/lib/reportsApi";
import { savedSearchesApi, type SavedSearch, type TicketFilter } from "@/lib/savedSearchesApi";

const PREVIEW_LIMIT = 10;
const EMPTY_FILTER: TicketFilter = { version: 1 };

function Notice({ testId, children }: { testId: string; children: React.ReactNode }) {
  return (
    <div className="m-6 rounded-lg border border-hairline bg-surface p-8 text-center text-sm text-ink" data-testid={testId}>
      {children}
    </div>
  );
}

function Editor({
  existing,
  onReload,
}: {
  /** The search being edited; undefined for a new one. */
  existing: SavedSearch | undefined;
  onReload: () => void;
}) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const id = useId();
  const isAdmin = user?.is_admin === true;
  const canShare = user?.can_share_reports === true;

  const [name, setName] = useState(existing?.name ?? "");
  const [criteria, setCriteria] = useState<TicketFilter>(existing?.criteria ?? EMPTY_FILTER);
  const [sortBy, setSortBy] = useState<TicketSortKey>(existing?.sort_by ?? "activity");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">(existing?.sort_order ?? "desc");
  const [includeArchived, setIncludeArchived] = useState(existing?.include_archived ?? false);
  const [sidebar, setSidebar] = useState(existing?.show_in_sidebar ?? true);
  const [showCount, setShowCount] = useState(existing?.show_count ?? false);
  const [groupIds, setGroupIds] = useState<number[]>(existing?.group_ids ?? []);
  const [tried, setTried] = useState(false);
  const [saveError, setSaveError] = useState<{ message: string; conflict: boolean } | null>(null);

  const fieldsQ = useQuery({
    queryKey: ["ticket-filters", "fields"],
    queryFn: ({ signal }) => savedSearchesApi.ticketFilterFields(signal),
    staleTime: 5 * 60_000,
  });
  const fields = useMemo(() => fieldsFromTicketCatalog(fieldsQ.data ?? [], t), [fieldsQ.data, t]);
  const filterErrors = useMemo(() => ticketFilterErrors(criteria, fields), [criteria, fields]);
  const filterValid = fieldsQ.isSuccess && filterErrors.size === 0;

  // The count and the first matches follow the filter, not every keystroke of a value.
  const shown = useDebouncedValue({ criteria, sortBy, sortOrder, includeArchived });
  const shownValid = filterValid && JSON.stringify(shown.criteria) === JSON.stringify(criteria);
  const previewOn = fieldsQ.isSuccess && ticketFilterErrors(shown.criteria, fields).size === 0;

  const countQ = useQuery({
    queryKey: ["saved-search-editor", "count", shown.criteria, isAdmin && shown.includeArchived],
    queryFn: ({ signal }) => savedSearchesApi.countTickets(shown.criteria, signal, isAdmin && shown.includeArchived),
    enabled: previewOn,
    retry: false,
    staleTime: 30_000,
  });
  const matchesQ = useQuery({
    queryKey: ["saved-search-editor", "matches", shown],
    queryFn: ({ signal }) =>
      savedSearchesApi.queryTickets(
        {
          filter: shown.criteria,
          sort: shown.sortBy,
          order: shown.sortOrder,
          limit: PREVIEW_LIMIT,
          include_archived: shown.includeArchived,
        },
        signal,
      ),
    enabled: previewOn,
    retry: false,
    staleTime: 30_000,
  });

  const nameMissing = tried && name.trim() === "";
  const invalidFilter = tried && !filterValid;

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: name.trim(),
        criteria,
        sort_by: sortBy,
        sort_order: sortOrder,
        include_archived: isAdmin && includeArchived,
        show_in_sidebar: sidebar,
        show_count: sidebar && showCount,
        group_ids: groupIds,
      };
      return existing ? savedSearchesApi.update(existing.id, { ...body, version: existing.version }) : savedSearchesApi.create(body);
    },
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: SAVED_SEARCHES_KEY });
      void queryClient.invalidateQueries({ queryKey: ["tickets", "saved-search"] });
      void navigate({ to: "/agent/searches/$searchId", params: { searchId: String(saved.id) }, search: {} });
    },
    onError: (err) => {
      const conflict = err instanceof ApiError && err.status === 409;
      setSaveError({
        message: conflict ? t("savedSearches.conflict") : reportErrorMessage(err, t("savedSearches.saveFailed")),
        conflict,
      });
    },
  });

  const submit = () => {
    if (save.isPending) return;
    setTried(true);
    if (name.trim() === "" || !filterValid) return;
    setSaveError(null);
    save.mutate();
  };

  const count = countQ.data?.count;
  const items = matchesQ.data?.items ?? [];
  const cancelTo = existing ? `/agent/searches/${existing.id}` : "/agent";

  return (
    <form
      className="mx-auto w-full max-w-[96rem] space-y-4 px-4 py-6"
      data-testid="saved-search-editor"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1 basis-72">
          <h1 className="font-display text-2xl font-semibold tracking-tight text-ink">
            {existing ? t("savedSearches.editTitle") : t("savedSearches.newTitle")}
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => void navigate({ to: cancelTo })} data-testid="saved-search-editor-cancel">
            {t("savedSearches.cancel")}
          </Button>
          <Button variant="primary" type="submit" disabled={save.isPending} data-testid="saved-search-editor-save">
            {save.isPending ? t("savedSearches.saving") : t("savedSearches.save")}
          </Button>
        </div>
      </div>

      {saveError && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2">
          <p className="text-sm text-ink" role="alert" data-testid="saved-search-editor-error">
            {saveError.message}
          </p>
          {saveError.conflict && (
            <Button size="sm" onClick={onReload} data-testid="saved-search-editor-reload">
              {t("savedSearches.reload")}
            </Button>
          )}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="space-y-5">
          <div className="space-y-1">
            <label htmlFor={`${id}-name`} className="block text-xs font-medium text-muted">
              {t("savedSearches.name")}
            </label>
            <input
              id={`${id}-name`}
              className={`${inputCls} w-full`}
              value={name}
              maxLength={200}
              placeholder={t("savedSearches.namePlaceholder")}
              onChange={(e) => setName(e.target.value)}
              aria-invalid={nameMissing || undefined}
              aria-describedby={nameMissing ? `${id}-name-error` : undefined}
              data-testid="saved-search-editor-name"
            />
            {nameMissing && (
              <p id={`${id}-name-error`} className="text-xs text-danger" data-testid="saved-search-editor-name-error">
                {t("savedSearches.nameRequired")}
              </p>
            )}
          </div>

          <section aria-labelledby={`${id}-conditions`} className="space-y-2">
            <h2 id={`${id}-conditions`} className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("savedSearches.conditions")}
            </h2>
            {fieldsQ.isPending ? (
              <Spinner />
            ) : (
              <FilterBuilder fields={fields} value={criteria} onChange={setCriteria} />
            )}
            {invalidFilter && (
              <p className="text-xs text-danger" role="alert" data-testid="saved-search-editor-filter-error">
                {t("savedSearches.invalidFilter")}
              </p>
            )}
          </section>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <label htmlFor={`${id}-sort`} className="block text-xs font-medium text-muted">
                {t("savedSearches.sort")}
              </label>
              <SelectField
                id={`${id}-sort`}
                items={TICKET_SORT_KEYS.map((k) => ({ value: k, label: t(`savedSearches.sortKeys.${k}`) }))}
                value={sortBy}
                onChange={setSortBy}
                testId="saved-search-editor-sort"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor={`${id}-order`} className="block text-xs font-medium text-muted">
                {t("savedSearches.order")}
              </label>
              <SelectField
                id={`${id}-order`}
                items={[
                  { value: "asc", label: t("savedSearches.asc") },
                  { value: "desc", label: t("savedSearches.desc") },
                ]}
                value={sortOrder}
                onChange={setSortOrder}
                testId="saved-search-editor-order"
              />
            </div>
          </div>

          <div className="space-y-3">
            {isAdmin && (
              <div className="flex items-center justify-between gap-3">
                <label htmlFor={`${id}-archived`} className="text-sm text-ink">
                  {t("savedSearches.includeArchived")}
                </label>
                <Switch id={`${id}-archived`} checked={includeArchived} onChange={setIncludeArchived} testId="saved-search-editor-archived" />
              </div>
            )}
            <div className="flex items-center justify-between gap-3">
              <label htmlFor={`${id}-sidebar`} className="text-sm text-ink">
                {t("savedSearches.showInSidebar")}
              </label>
              <Switch id={`${id}-sidebar`} checked={sidebar} onChange={setSidebar} testId="saved-search-editor-sidebar" />
            </div>
            <div className="flex items-center justify-between gap-3">
              <label htmlFor={`${id}-count`} className={sidebar ? "text-sm text-ink" : "text-sm text-muted"}>
                {t("savedSearches.showCount")}
              </label>
              <Switch
                id={`${id}-count`}
                checked={sidebar && showCount}
                onChange={setShowCount}
                disabled={!sidebar}
                testId="saved-search-editor-count-switch"
              />
            </div>
          </div>

          <SearchShareFields value={groupIds} onChange={setGroupIds} canShare={canShare} testId="saved-search-editor-share" />
        </div>

        <section aria-labelledby={`${id}-preview`} className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-baseline gap-3">
            <h2 id={`${id}-preview`} className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("savedSearches.preview")}
            </h2>
            <p className="text-sm text-ink" role="status" aria-live="polite" data-testid="saved-search-editor-count-line">
              {previewOn && countQ.isPending && <Spinner />}
              {shownValid && count != null && (
                <span data-testid="saved-search-editor-count">{t("savedSearches.matches", { count })}</span>
              )}
              {previewOn && countQ.isError && (
                <span className="text-muted" data-testid="saved-search-editor-count-error">
                  {t("savedSearches.countFailed")}
                </span>
              )}
            </p>
          </div>
          {previewOn && matchesQ.isSuccess && items.length === 0 ? (
            <p className="rounded-lg border border-hairline bg-surface px-4 py-6 text-center text-sm text-muted" data-testid="saved-search-editor-empty">
              {t("savedSearches.previewEmpty")}
            </p>
          ) : (
            <div data-testid="saved-search-editor-matches">
              <TicketTable
                items={items}
                total={items.length}
                offset={0}
                limit={PREVIEW_LIMIT}
                sort={toTableSortKey(sortBy)}
                order={sortOrder}
                isLoading={previewOn && matchesQ.isPending}
                onSortChange={(s, o) => {
                  setSortBy(toTicketSortKey(s));
                  setSortOrder(o);
                }}
                onPageChange={() => {}}
              />
            </div>
          )}
        </section>
      </div>
    </form>
  );
}

/** `/agent/searches/new` and `/agent/searches/$searchId/edit`: create or change a saved search. */
export function SavedSearchEditorPage() {
  const { t } = useTranslation();
  const { searchId } = useParams({ strict: false }) as { searchId?: string };
  const editing = searchId !== undefined;
  const id = Number(searchId);
  const [nonce, setNonce] = useState(0);
  const searchQ = useQuery({
    queryKey: [...SAVED_SEARCHES_KEY, "get", id, "editor"],
    queryFn: ({ signal }) => savedSearchesApi.get(id, signal),
    enabled: editing && Number.isInteger(id),
    retry: false,
    // The editor owns its copy: a refetch on focus must not replace what is being edited.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    gcTime: 0,
  });

  if (editing && (!Number.isInteger(id) || (searchQ.error instanceof ApiError && searchQ.error.status === 404))) {
    return <Notice testId="saved-search-editor-not-found">{t("savedSearches.notFound")}</Notice>;
  }
  if (editing && searchQ.isError) {
    return <Notice testId="saved-search-editor-load-error">{reportErrorMessage(searchQ.error, t("savedSearches.loadError"))}</Notice>;
  }
  if (editing && !searchQ.data) {
    return (
      <div className="flex justify-center py-16" role="status" aria-label={t("common.loading")}>
        <Spinner />
      </div>
    );
  }
  if (editing && searchQ.data && !searchQ.data.can_edit) {
    return (
      <Notice testId="saved-search-editor-no-right">
        <p>{t("savedSearches.noRight")}</p>
        <Link
          to="/agent/searches/$searchId"
          params={{ searchId: String(searchQ.data.id) }}
          search={{}}
          className="mt-4 inline-flex rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink hover:bg-surface-subtle"
        >
          {t("savedSearches.back")}
        </Link>
      </Notice>
    );
  }
  return (
    <Editor
      key={`${searchId ?? "new"}:${nonce}`}
      existing={editing ? searchQ.data : undefined}
      onReload={() => void searchQ.refetch().then(() => setNonce((n) => n + 1))}
    />
  );
}
