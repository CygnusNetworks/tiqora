import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BulkEditDialog } from "@/components/cmdb/BulkEditDialog";
import { CiExportDialog } from "@/components/cmdb/CiExportDialog";
import { CiFilterBar } from "@/components/cmdb/CiFilterBar";
import { CiSavedSearches } from "@/components/cmdb/CiSavedSearches";
import { CiStateBadge } from "@/components/cmdb/CiStateBadge";
import { CmdbModeNotice } from "@/components/cmdb/CmdbModeNotice";
import {
  DEPLOYMENT_CATALOG,
  INCIDENT_CATALOG,
  PAGE_SIZE,
  type CmdbListSearch,
  emptyFilterState,
  fromSavedCriteria,
  toCriteria,
  toSearchBody,
  type CiFilterState,
  type CiSort,
} from "@/components/cmdb/ciFilterModel";
import { JobDialog } from "@/components/jobs/JobDialog";
import { Button } from "@/components/ui/Button";
import { SelectMenu } from "@/components/ui/SelectMenu";
import { Spinner } from "@/components/ui/Spinner";
import { cmdbApi, useCmdbSavedSearches, useCmdbStatus, type CiListItem, type CmdbSavedSearch } from "@/lib/cmdbApi";
import { formatDateTime } from "@/lib/format";

function SortHeader({ label, column, sort, onSort }: { label: string; column: CiSort["sort"]; sort: CiSort; onSort: (c: CiSort["sort"]) => void }) {
  const active = sort.sort === column;
  return (
    <th scope="col" aria-sort={active ? (sort.order === "asc" ? "ascending" : "descending") : "none"} className="px-3 py-2 text-left font-medium">
      <button type="button" onClick={() => onSort(column)} className="inline-flex items-center gap-1 hover:text-ink" data-testid={`ci-sort-${column}`}>
        {label}
        <span aria-hidden className="text-[10px]">
          {active ? (sort.order === "asc" ? "▲" : "▼") : ""}
        </span>
      </button>
    </th>
  );
}

const LINK_BTN =
  "inline-flex items-center rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-subtle";
const PRIMARY_BTN =
  "inline-flex items-center rounded-md border border-transparent bg-accent px-3 py-1.5 text-sm font-medium text-accent-ink hover:opacity-90 disabled:pointer-events-none disabled:opacity-50";

/** CMDB list: search, filters, saved searches, bulk edit and export. */
export function CmdbListPage() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate({ from: "/agent/cmdb" });
  const search = useSearch({ from: "/agent/cmdb" }) as CmdbListSearch;
  const statusQ = useCmdbStatus();
  const status = statusQ.data;
  const active = status?.mode === "tiqora";

  const sort: CiSort = { sort: search.sort ?? "number", order: search.order ?? "asc" };
  const page = search.page ?? 1;
  const [filters, setFilters] = useState<CiFilterState>(() => ({
    ...emptyFilterState(),
    classId: search.class ?? null,
    q: search.q ?? "",
  }));

  const setSearch = (patch: Partial<CmdbListSearch>) => {
    void navigate({ search: (prev: CmdbListSearch) => ({ ...prev, ...patch }), replace: true });
  };

  // a user edit of the filter: back to page 1, the stored search no longer matches
  const changeFilters = (next: CiFilterState) => {
    setFilters(next);
    setSearch({ class: next.classId ?? undefined, q: next.q.trim() || undefined, page: undefined, saved: undefined });
  };

  const classesQ = useQuery({ queryKey: ["cmdb", "classes"], queryFn: () => cmdbApi.classes(), enabled: active, staleTime: 60_000 });
  const classes = useMemo(() => (classesQ.data ?? []).filter((c) => c.valid), [classesQ.data]);
  const writable = classes.filter((c) => c.can_write);

  const deploymentQ = useQuery({ queryKey: ["cmdb", "catalog", DEPLOYMENT_CATALOG], queryFn: () => cmdbApi.catalog(DEPLOYMENT_CATALOG), enabled: active, staleTime: 60_000 });
  const incidentQ = useQuery({ queryKey: ["cmdb", "catalog", INCIDENT_CATALOG], queryFn: () => cmdbApi.catalog(INCIDENT_CATALOG), enabled: active, staleTime: 60_000 });
  const deploymentItems = (deploymentQ.data ?? []).filter((i) => i.valid);
  const incidentItems = (incidentQ.data ?? []).filter((i) => i.valid);

  const fieldsQ = useQuery({
    queryKey: ["cmdb", "filter-fields", filters.classId],
    queryFn: () => cmdbApi.filterFields(filters.classId ?? undefined),
    enabled: active && filters.classId != null,
    staleTime: 60_000,
  });
  const attrFields = filters.classId != null ? (fieldsQ.data ?? []).filter((f) => f.key.startsWith("attr.")) : [];

  // saved searches: a `saved` id in the URL is applied once, when the list is there
  const savedQ = useCmdbSavedSearches();
  const appliedSaved = useRef<number | null>(null);
  const applySaved = (s: CmdbSavedSearch | null) => {
    appliedSaved.current = s?.id ?? null;
    if (!s) {
      setSearch({ saved: undefined });
      return;
    }
    const next = fromSavedCriteria(s.criteria);
    setFilters(next);
    setSearch({ saved: s.id, class: next.classId ?? undefined, q: next.q.trim() || undefined, sort: s.criteria.sort, order: s.criteria.order, page: undefined });
  };
  useEffect(() => {
    if (!active || search.saved == null || appliedSaved.current === search.saved) return;
    const found = savedQ.data?.find((s) => s.id === search.saved);
    if (found) applySaved(found);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, search.saved, savedQ.data]);

  const body = useMemo(() => toSearchBody(filters, page, sort), [filters, page, sort.sort, sort.order]); // eslint-disable-line react-hooks/exhaustive-deps
  const listQ = useQuery({
    queryKey: ["cmdb", "cis", body],
    queryFn: () => cmdbApi.search(body),
    enabled: active,
    placeholderData: keepPreviousData,
  });
  const items: CiListItem[] = listQ.data?.items ?? [];
  const total = listQ.data?.total ?? 0;
  const capped = listQ.data?.total_capped === true;

  // selection (current page only; any change of the search clears it)
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const bodyKey = JSON.stringify(body);
  useEffect(() => setSelected(new Set()), [bodyKey]);
  const allOnPage = items.length > 0 && items.every((i) => selected.has(i.id));
  const toggleOne = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleAll = () => setSelected(allOnPage ? new Set() : new Set(items.map((i) => i.id)));

  // bulk + export
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkJob, setBulkJob] = useState<number | null>(null);
  const [bulkSummary, setBulkSummary] = useState<string | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportJob, setExportJob] = useState<number | null>(null);

  const onSort = (column: CiSort["sort"]) =>
    setSearch({ sort: column, order: sort.sort === column && sort.order === "asc" ? "desc" : "asc", page: undefined });

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const dateOf = (v: string) => formatDateTime(v, i18n.language);
  const emptyState = active && listQ.isSuccess && items.length === 0;

  const cells = (ci: CiListItem) => ({
    link: (
      <Link to="/agent/cmdb/$ciId" params={{ ciId: String(ci.id) }} className="font-medium text-accent hover:underline">
        {ci.number}
      </Link>
    ),
    dep: <CiStateBadge kind="deployment" functionality={ci.deployment_functionality} label={ci.deployment_state} />,
    inc: <CiStateBadge kind="incident" functionality={ci.incident_functionality} label={ci.incident_state} />,
  });

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 sm:p-6" data-testid="cmdb-list-page">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="font-display text-xl font-bold text-ink">{t("cmdb.title")}</h1>
        {active && (
          <div className="flex flex-wrap items-center gap-2">
            {writable.length === 1 ? (
              <Link to="/agent/cmdb/new" search={{ class: writable[0].id }} className={PRIMARY_BTN} data-testid="cmdb-new">
                {t("cmdb.list.new")}
              </Link>
            ) : (
              <SelectMenu<number>
                items={writable.map((c) => ({ value: c.id, label: c.name }))}
                value={null}
                onSelect={(id) => void navigate({ to: "/agent/cmdb/new", search: { class: id } })}
                panelTestId="cmdb-new-menu"
                trigger={({ ref, toggleProps }) => (
                  <button ref={ref} type="button" className={PRIMARY_BTN} disabled={writable.length === 0} data-testid="cmdb-new" {...toggleProps}>
                    {t("cmdb.list.new")}
                  </button>
                )}
              />
            )}
            <Link to="/agent/cmdb/import" className={LINK_BTN} data-testid="cmdb-import">
              {t("cmdb.list.import")}
            </Link>
            <Button
              onClick={() => setExportOpen(true)}
              disabled={filters.classId == null}
              title={filters.classId == null ? t("cmdb.list.export.needsClass") : undefined}
              data-testid="cmdb-export"
            >
              {t("cmdb.list.export.button")}
            </Button>
          </div>
        )}
      </div>

      {status && <CmdbModeNotice status={status} />}

      {active && (
        <>
          <CiFilterBar state={filters} onChange={changeFilters} classes={classes} attrFields={attrFields} deploymentItems={deploymentItems} />

          <div className="flex flex-wrap items-center justify-between gap-2">
            <CiSavedSearches selectedId={search.saved ?? null} criteria={toCriteria(filters, sort)} onSelect={applySaved} />
            <div className="flex items-center gap-3">
              {listQ.isFetching && <Spinner />}
              {listQ.isSuccess && (
                <span className="text-sm text-muted" data-testid="cmdb-total">
                  {capped ? t("cmdb.list.totalCapped") : t("cmdb.list.total", { count: total })}
                </span>
              )}
              <Button size="sm" disabled={selected.size === 0} onClick={() => setBulkOpen(true)} data-testid="cmdb-bulk">
                {t("cmdb.list.bulk.open")}
                {selected.size > 0 ? ` (${selected.size})` : ""}
              </Button>
            </div>
          </div>

          {bulkSummary && (
            <p role="status" className="rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink" data-testid="cmdb-bulk-summary">
              {bulkSummary}
            </p>
          )}

          {listQ.isError && (
            <p role="alert" className="text-sm text-danger">
              {t("cmdb.list.loadFailed")}
            </p>
          )}

          {emptyState && (
            <p className="rounded-md border border-dashed border-hairline px-4 py-8 text-center text-sm text-muted" data-testid="cmdb-empty">
              {t("cmdb.list.empty")}
            </p>
          )}

          {items.length > 0 && (
            <>
              <div className="hidden overflow-x-auto rounded-md border border-hairline md:block">
                <table className="w-full text-sm" data-testid="cmdb-table">
                  <thead className="bg-surface-subtle text-xs text-muted">
                    <tr>
                      <th scope="col" className="w-8 px-3 py-2">
                        <input type="checkbox" checked={allOnPage} onChange={toggleAll} aria-label={t("cmdb.list.selectAll")} data-testid="ci-select-all" />
                      </th>
                      <SortHeader label={t("cmdb.list.number")} column="number" sort={sort} onSort={onSort} />
                      <SortHeader label={t("cmdb.list.name")} column="name" sort={sort} onSort={onSort} />
                      <SortHeader label={t("cmdb.list.class")} column="class" sort={sort} onSort={onSort} />
                      <SortHeader label={t("cmdb.list.deploymentState")} column="deployment_state" sort={sort} onSort={onSort} />
                      <SortHeader label={t("cmdb.list.incidentState")} column="incident_state" sort={sort} onSort={onSort} />
                      <SortHeader label={t("cmdb.list.changed")} column="changed_at" sort={sort} onSort={onSort} />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((ci) => {
                      const c = cells(ci);
                      return (
                        <tr key={ci.id} className="border-t border-hairline" data-testid={`ci-row-${ci.id}`}>
                          <td className="px-3 py-2">
                            <input type="checkbox" checked={selected.has(ci.id)} onChange={() => toggleOne(ci.id)} aria-label={t("cmdb.list.select", { name: ci.number })} data-testid={`ci-check-${ci.id}`} />
                          </td>
                          <td className="px-3 py-2 font-mono text-xs">{c.link}</td>
                          <td className="px-3 py-2 text-ink">{ci.name}</td>
                          <td className="px-3 py-2 text-muted">{ci.class_name}</td>
                          <td className="px-3 py-2">{c.dep}</td>
                          <td className="px-3 py-2">{c.inc}</td>
                          <td className="px-3 py-2 text-xs text-muted">{dateOf(ci.changed_at)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <ul className="space-y-2 md:hidden" data-testid="cmdb-cards">
                {items.map((ci) => {
                  const c = cells(ci);
                  return (
                    <li key={ci.id} className="rounded-md border border-hairline bg-surface p-3" data-testid={`ci-card-${ci.id}`}>
                      <div className="flex items-start gap-3">
                        <input type="checkbox" className="mt-1" checked={selected.has(ci.id)} onChange={() => toggleOne(ci.id)} aria-label={t("cmdb.list.select", { name: ci.number })} data-testid={`ci-card-check-${ci.id}`} />
                        <div className="min-w-0 flex-1 space-y-1">
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-mono text-xs">{c.link}</span>
                            <span className="text-xs text-muted">{ci.class_name}</span>
                          </div>
                          <p className="truncate text-sm text-ink">{ci.name}</p>
                          <div className="flex flex-wrap items-center gap-1.5">
                            {c.dep}
                            {c.inc}
                          </div>
                          <p className="text-xs text-muted">{dateOf(ci.changed_at)}</p>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>

              {(pages > 1 || page > 1) && (
                <nav className="flex items-center justify-between gap-2" aria-label={t("cmdb.list.paging")}>
                  <Button size="sm" disabled={page <= 1} onClick={() => setSearch({ page: page - 1 })} data-testid="cmdb-prev">
                    {t("common.prev")}
                  </Button>
                  <span className="text-xs text-muted">{capped ? t("cmdb.list.pageNo", { page }) : t("cmdb.list.pageOf", { page, pages })}</span>
                  <Button
                    size="sm"
                    disabled={capped ? items.length < PAGE_SIZE : page >= pages}
                    onClick={() => setSearch({ page: page + 1 })}
                    data-testid="cmdb-next"
                  >
                    {t("common.next")}
                  </Button>
                </nav>
              )}
            </>
          )}

          <BulkEditDialog
            open={bulkOpen}
            ciIds={[...selected]}
            deploymentItems={deploymentItems}
            incidentItems={incidentItems}
            onClose={() => setBulkOpen(false)}
            onStarted={(jobId) => {
              setBulkOpen(false);
              setBulkSummary(null);
              setBulkJob(jobId);
            }}
          />
          <CiExportDialog
            open={exportOpen && filters.classId != null}
            request={{ class_id: filters.classId ?? 0, q: body.q ?? null, filter: body.filter ?? null }}
            onClose={() => setExportOpen(false)}
            onStarted={(jobId) => {
              setExportOpen(false);
              setExportJob(jobId);
            }}
          />
          <JobDialog
            jobId={bulkJob}
            title={t("jobs.kinds.core.cmdb_bulk")}
            testId="bulk-job"
            onClose={() => setBulkJob(null)}
            onSucceeded={(job) => {
              const r = (job.result ?? {}) as { updated?: unknown; skipped?: unknown };
              setBulkSummary(t("cmdb.list.bulk.result", { updated: Number(r.updated ?? 0), skipped: Number(r.skipped ?? 0) }));
              setSelected(new Set());
              void qc.invalidateQueries({ queryKey: ["cmdb", "cis"] });
            }}
          />
          <JobDialog jobId={exportJob} title={t("jobs.kinds.core.cmdb_export")} testId="export-job" onClose={() => setExportJob(null)} />
        </>
      )}
    </div>
  );
}
