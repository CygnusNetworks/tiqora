import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api, ApiError, type CustomerDirectoryEntry } from "@/lib/api";
import { useAuth } from "@/auth/AuthContext";
import {
  DataTable,
  type DataTableColumn,
  type DataTableSortState,
} from "@/components/admin/DataTable";
import { CustomerCompanyCell, CustomerNameCell } from "@/components/customers/CustomerCells";
import { saveSelectedVcards, VCARD_EXPORT_MAX } from "@/components/customers/vcardExport";
import { Button } from "@/components/ui/Button";
import { Popover } from "@/components/ui/Popover";
import { usePopoverClose } from "@/components/ui/popoverContext";
import { Spinner } from "@/components/ui/Spinner";
import { DownloadIcon, SearchIcon } from "@/components/ui/icons";

const PAGE_SIZE = 50;

export type CustomerDirectorySearch = {
  q?: string;
  /** Company filter: customer_id … */
  company?: string;
  /** … and its display name (not sent to the API). */
  company_name?: string;
  page?: number;
  invalid?: boolean;
  /** Server sort key (name, company, phone, city); absent = name order. */
  sort?: string;
  order?: "asc" | "desc";
};

function useDebounced<T>(value: T, ms: number): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const h = window.setTimeout(() => setOut(value), ms);
    return () => window.clearTimeout(h);
  }, [value, ms]);
  return out;
}

const linkButtonClass =
  "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-accent hover:bg-accent-dim focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent";
const disabledLinkClass =
  "inline-flex cursor-not-allowed items-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-muted";

/**
 * Agent customer directory ("Kunden"): every customer user, name first, with
 * a company filter and vCard downloads per contact, per company, for the
 * filtered list and for a selection. Needs the customer_directory feature.
 */
export function CustomerDirectoryPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate({ from: "/agent/customers" });
  const search = useSearch({ from: "/agent/customers" });
  const allowed = Boolean(user?.can_use_customer_directory);

  const page = search.page ?? 1;
  const [query, setQuery] = useState(search.q ?? "");
  const debouncedQuery = useDebounced(query.trim(), 300);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  // Typing writes the (debounced) term to the URL and resets to page 1.
  useEffect(() => {
    if ((search.q ?? "") === debouncedQuery) return;
    void navigate({
      search: (prev: CustomerDirectorySearch) => ({
        ...prev,
        q: debouncedQuery || undefined,
        page: undefined,
      }),
      replace: true,
    });
  }, [debouncedQuery, search.q, navigate]);

  const filter = {
    search: search.q,
    customerId: search.company,
    valid: search.invalid ? ("all" as const) : ("valid" as const),
  };

  const sortState: DataTableSortState = {
    sort: search.sort ?? null,
    order: search.order ?? "asc",
  };

  const listQ = useQuery({
    queryKey: ["customer-directory", filter, page, sortState],
    queryFn: ({ signal }) =>
      api.listCustomerDirectory(
        {
          ...filter,
          page,
          pageSize: PAGE_SIZE,
          sort: sortState.sort ?? undefined,
          order: sortState.order,
        },
        signal,
      ),
    enabled: allowed,
    placeholderData: keepPreviousData,
  });

  const rows = useMemo(() => listQ.data?.items ?? [], [listQ.data]);
  const total = listQ.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const setSearch = (patch: Partial<CustomerDirectorySearch>) =>
    void navigate({
      search: (prev: CustomerDirectorySearch) => ({ ...prev, page: undefined, ...patch }),
    });

  const filterByCompany = (customerId: string, name: string | null | undefined) =>
    setSearch({ company: customerId, company_name: name || undefined });

  const columns: DataTableColumn<CustomerDirectoryEntry>[] = [
    {
      key: "name",
      header: t("customerDirectory.name"),
      sortable: true,
      render: (r) => (
        <CustomerNameCell
          firstName={r.first_name}
          lastName={r.last_name}
          email={r.email}
          login={r.login}
          title={(name) => (
            <Link
              to="/agent/customers/$login"
              params={{ login: r.login }}
              className="truncate hover:underline"
              data-testid={`customer-directory-open-${r.login}`}
            >
              {name}
            </Link>
          )}
        />
      ),
    },
    {
      key: "company",
      header: t("customerDirectory.company"),
      sortable: true,
      render: (r) =>
        search.company ? (
          <CustomerCompanyCell companyName={r.company_name} customerId={r.customer_id} />
        ) : (
          <button
            type="button"
            className="min-w-0 rounded-sm text-left hover:underline"
            title={t("customerDirectory.companyFilter")}
            onClick={() => filterByCompany(r.customer_id, r.company_name)}
          >
            <CustomerCompanyCell companyName={r.company_name} customerId={r.customer_id} />
          </button>
        ),
    },
    {
      key: "phone",
      header: t("customerDirectory.phone"),
      hideBelow: "lg",
      sortable: true,
      render: (r) => (
        <span className="tabular-nums text-muted">{r.phone || r.mobile || "–"}</span>
      ),
    },
    {
      key: "city",
      header: t("customerDirectory.city"),
      hideBelow: "xl",
      sortable: true,
      render: (r) => <span className="text-muted">{r.city || "–"}</span>,
    },
    {
      key: "vcard",
      header: "",
      className: "w-10",
      render: (r) => (
        <a
          href={api.customerVcardUrl(r.login)}
          download
          className="inline-flex rounded-md p-1 text-accent hover:bg-accent-dim"
          title={t("customerCentre.downloadVcard")}
          aria-label={t("customerCentre.downloadVcard")}
          data-testid={`customer-directory-vcard-${r.login}`}
        >
          <DownloadIcon className="text-[15px]" />
        </a>
      ),
    },
  ];

  if (!allowed) {
    return (
      <div
        className="m-6 rounded-lg border border-hairline bg-surface p-8 text-center"
        data-testid="customer-directory-denied"
      >
        <h1 className="font-display text-lg font-semibold text-ink">
          {t("customerDirectory.deniedTitle")}
        </h1>
        <p className="mt-2 text-sm text-muted">{t("customerDirectory.deniedBody")}</p>
      </div>
    );
  }

  const pageLogins = rows.map((r) => r.login);
  const allPageSelected = pageLogins.length > 0 && pageLogins.every((l) => selected.has(l));
  const somePageSelected = !allPageSelected && pageLogins.some((l) => selected.has(l));
  const listTooBig = total > VCARD_EXPORT_MAX;

  const exportSelection = async () => {
    setExportError(null);
    if (selected.size > VCARD_EXPORT_MAX) {
      setExportError(t("customerDirectory.exportTooMany", { max: VCARD_EXPORT_MAX }));
      return;
    }
    setExportBusy(true);
    try {
      await saveSelectedVcards(Array.from(selected));
      setSelected(new Set());
    } catch (err) {
      const message = err instanceof ApiError || err instanceof Error ? err.message : String(err);
      setExportError(t("customerDirectory.exportFailed", { message }));
    } finally {
      setExportBusy(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-6xl space-y-3 px-4 py-5" data-testid="customer-directory-page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">
            {t("customerDirectory.title")}
          </h1>
          <p className="text-sm text-muted">{t("customerDirectory.subtitle")}</p>
        </div>
        {total > 0 &&
          (listTooBig ? (
            <span
              className={disabledLinkClass}
              title={t("customerDirectory.exportListTooMany", { max: VCARD_EXPORT_MAX })}
              data-testid="customer-directory-export-list-disabled"
            >
              <DownloadIcon className="text-[15px]" />
              {t("customerDirectory.exportList", { count: total })}
            </span>
          ) : (
            <a
              href={api.customerDirectoryVcardsUrl(filter)}
              download
              className={linkButtonClass}
              data-testid="customer-directory-export-list"
            >
              <DownloadIcon className="text-[15px]" />
              {t("customerDirectory.exportList", { count: total })}
            </a>
          ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="relative min-w-[14rem] flex-1">
          <span className="sr-only">{t("customerDirectory.searchPlaceholder")}</span>
          <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[14px] text-muted" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("customerDirectory.searchPlaceholder")}
            data-testid="customer-directory-search"
            className="w-full rounded-md border border-hairline bg-surface py-1.5 pl-8 pr-2 text-sm text-ink focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          />
        </label>
        {search.company ? (
          <span
            className="inline-flex items-center gap-1 rounded-full border border-accent bg-accent-dim py-1 pl-3 pr-1 text-sm text-accent"
            data-testid="customer-directory-company-chip"
          >
            <span className="max-w-[16rem] truncate">{search.company_name || search.company}</span>
            <button
              type="button"
              className="rounded-full px-1.5 leading-none hover:bg-accent/15"
              aria-label={t("customerDirectory.clearCompany")}
              title={t("customerDirectory.clearCompany")}
              data-testid="customer-directory-company-clear"
              onClick={() => setSearch({ company: undefined, company_name: undefined })}
            >
              ×
            </button>
          </span>
        ) : (
          <CompanyPicker onPick={(id, name) => filterByCompany(id, name)} />
        )}
        <label className="inline-flex items-center gap-1.5 text-sm text-muted">
          <input
            type="checkbox"
            checked={Boolean(search.invalid)}
            onChange={(e) => setSearch({ invalid: e.target.checked || undefined })}
            data-testid="customer-directory-invalid"
          />
          {t("customerDirectory.includeInvalid")}
        </label>
      </div>

      {search.company && (
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-hairline bg-surface px-3 py-2"
          data-testid="customer-directory-company-banner"
        >
          <span className="font-medium text-ink">{search.company_name || search.company}</span>
          <span className="font-mono text-xs text-muted">{search.company}</span>
          <span className="text-xs text-muted">
            {t("customerDirectory.companyContacts", { count: total })}
          </span>
          <Link
            to="/agent/search"
            search={{
              customer_id: search.company,
              customer_label: search.company_name || search.company,
            }}
            className="text-sm text-accent hover:underline"
          >
            {t("customerDirectory.companyTickets")}
          </Link>
          <a
            href={api.companyVcardsUrl(search.company)}
            download
            className={`${linkButtonClass} ml-auto`}
            data-testid="customer-directory-export-company"
          >
            <DownloadIcon className="text-[15px]" />
            {t("customerDirectory.exportCompany", { count: total })}
          </a>
        </div>
      )}

      {(selected.size > 0 || exportError) && (
        <div
          className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-lg border border-accent/50 bg-accent-dim px-3 py-1.5"
          role="toolbar"
          aria-label={t("admin.bulk.selected", { count: selected.size })}
          data-testid="customer-directory-selection-bar"
        >
          {selected.size > 0 && (
            <>
              <span className="mr-auto text-sm font-medium text-ink">
                {t("admin.bulk.selected", { count: selected.size })}
              </span>
              <Button
                size="sm"
                variant="primary"
                disabled={exportBusy}
                onClick={() => void exportSelection()}
                data-testid="customer-directory-export-selection"
              >
                {exportBusy ? <Spinner className="h-3.5 w-3.5" /> : <DownloadIcon className="text-[14px]" />}
                {t("customerDirectory.exportSelection")}
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setSelected(new Set())}>
                {t("customerDirectory.clearSelection")}
              </Button>
            </>
          )}
          {exportError && (
            <p className="w-full text-xs text-danger" data-testid="customer-directory-export-error">
              {exportError}
            </p>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <span className="font-mono tabular-nums" data-testid="customer-directory-count">
          {listQ.isLoading ? "…" : t("customerDirectory.companyContacts", { count: total })}
        </span>
        {totalPages > 1 && (
          <div className="inline-flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={page <= 1}
              onClick={() =>
                void navigate({
                  search: (prev: CustomerDirectorySearch) => ({
                    ...prev,
                    page: page - 1 > 1 ? page - 1 : undefined,
                  }),
                })
              }
            >
              {t("admin.pagination.prev")}
            </Button>
            <span>{t("admin.pagination.page", { page, total: totalPages })}</span>
            <Button
              size="sm"
              variant="secondary"
              disabled={page >= totalPages}
              onClick={() =>
                void navigate({
                  search: (prev: CustomerDirectorySearch) => ({ ...prev, page: page + 1 }),
                })
              }
            >
              {t("admin.pagination.next")}
            </Button>
          </div>
        )}
      </div>

      {listQ.isError ? (
        <p className="rounded-lg border border-hairline bg-surface p-6 text-sm text-danger">
          {t("customerDirectory.loadError")}
        </p>
      ) : (
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.login}
          isLoading={listQ.isLoading}
          busy={listQ.isFetching && !listQ.isLoading}
          emptyLabel={t("customerDirectory.empty")}
          sort={sortState}
          onSortChange={(next) =>
            setSearch({
              sort: next.sort ?? undefined,
              order: next.sort && next.order === "desc" ? "desc" : undefined,
            })
          }
          isRowValid={search.invalid ? (r) => r.valid_id === 1 : undefined}
          selection={{
            selected,
            onToggle: (id) =>
              setSelected((prev) => {
                const next = new Set(prev);
                const login = String(id);
                if (next.has(login)) next.delete(login);
                else next.add(login);
                return next;
              }),
            onToggleAll: () =>
              setSelected((prev) => {
                const next = new Set(prev);
                for (const login of pageLogins) {
                  if (allPageSelected) next.delete(login);
                  else next.add(login);
                }
                return next;
              }),
            allSelected: allPageSelected,
            someSelected: somePageSelected,
          }}
          testId="customer-directory-table"
        />
      )}
    </div>
  );
}

function CompanyPicker({ onPick }: { onPick: (customerId: string, name: string) => void }) {
  const { t } = useTranslation();
  return (
    <Popover
      label={t("customerDirectory.companyFilter")}
      panelClassName="w-80"
      panelTestId="customer-directory-company-panel"
      trigger={({ ref, toggleProps }) => (
        <button
          ref={ref}
          type="button"
          {...toggleProps}
          className="rounded-md border border-hairline bg-surface px-2.5 py-1.5 text-sm text-muted hover:text-ink"
          data-testid="customer-directory-company-filter"
        >
          {t("customerDirectory.companyFilter")}
        </button>
      )}
    >
      <CompanyPickerPanel onPick={onPick} />
    </Popover>
  );
}

function CompanyPickerPanel({ onPick }: { onPick: (customerId: string, name: string) => void }) {
  const { t } = useTranslation();
  const close = usePopoverClose();
  const [term, setTerm] = useState("");
  const debounced = useDebounced(term.trim(), 250);
  const companiesQ = useQuery({
    queryKey: ["customer-directory", "companies", debounced],
    queryFn: ({ signal }) => api.searchCustomerDirectoryCompanies(debounced, signal),
  });
  const companies = companiesQ.data ?? [];
  return (
    <div className="space-y-2 p-2">
      <input
        type="search"
        autoFocus
        value={term}
        onChange={(e) => setTerm(e.target.value)}
        placeholder={t("customerDirectory.companySearch")}
        data-testid="customer-directory-company-search"
        className="w-full rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
      />
      {companiesQ.isLoading ? (
        <div className="flex justify-center py-3">
          <Spinner className="h-4 w-4" />
        </div>
      ) : companies.length === 0 ? (
        <p className="px-1 py-2 text-xs text-muted">{t("customerDirectory.noCompanyMatch")}</p>
      ) : (
        <ul className="max-h-64 overflow-y-auto">
          {companies.map((co) => (
            <li key={co.customer_id}>
              <button
                type="button"
                className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-surface-subtle"
                data-testid={`customer-directory-company-option-${co.customer_id}`}
                onClick={() => {
                  onPick(co.customer_id, co.name);
                  close();
                }}
              >
                <span className="truncate text-ink">{co.name}</span>
                <span className="shrink-0 font-mono text-[11px] text-muted">{co.customer_id}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
