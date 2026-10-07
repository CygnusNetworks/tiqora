import { useEffect, useMemo, useState, type ReactNode } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { CompanyPanel } from "@/components/customers/CompanyPanel";
import { CustomerCallStrip } from "@/components/customers/CustomerCallStrip";
import {
  CustomerEditDrawer,
  type CustomerDrawerState,
} from "@/components/customers/CustomerEditDrawer";
import { CustomerPanel } from "@/components/customers/CustomerPanel";
import { customerName, initialsOf } from "@/components/customers/customerFormat";
import { VCARD_EXPORT_MAX } from "@/components/customers/vcardExport";
import { Avatar } from "@/components/ui/Avatar";
import { Spinner } from "@/components/ui/Spinner";
import { BuildingIcon, DownloadIcon, PlusIcon, SearchIcon } from "@/components/ui/icons";
import { api, type CustomerShortlistEntry } from "@/lib/api";
import { cn } from "@/lib/cn";
import { displayTimeZone } from "@/lib/timeZone";

const PAGE_SIZE = 30;

export type CustomerDirectorySearch = {
  q?: string;
  /** Left-rail tab; absent = people. */
  tab?: "companies";
  /** Selected customer user (login). */
  sel?: string;
  /** Selected company (customer_id), companies tab. */
  company?: string;
  invalid?: boolean;
};

function useDebounced<T>(value: T, ms: number): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const h = window.setTimeout(() => setOut(value), ms);
    return () => window.clearTimeout(h);
  }, [value, ms]);
  return out;
}

/** "10:42" today, "04.10." earlier — for the "Zuletzt" list. */
function shortWhen(iso: string, locale: string): string {
  const d = new Date(iso);
  const timeZone = displayTimeZone();
  const day = (x: Date) => x.toLocaleDateString(locale, { timeZone });
  if (day(d) === day(new Date())) {
    return d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", timeZone });
  }
  return d.toLocaleDateString(locale, { day: "2-digit", month: "2-digit", timeZone });
}

const tabClass = (on: boolean) =>
  cn(
    "flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
    on ? "bg-surface-subtle text-ink shadow-sm" : "text-muted hover:text-ink",
  );

/**
 * Agent customer workbench ("Kunden"): search on the left — people or
 * companies — with the agent's own recent and frequent customers when
 * nothing is typed; the selected customer or company on the right with its
 * ticket and call actions. A ringing call shows on top. Needs the
 * customer_directory feature; creating and editing needs customer_edit.
 */
export function CustomerDirectoryPage() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate({ from: "/agent/customers" });
  const search = useSearch({ from: "/agent/customers" });
  const allowed = Boolean(user?.can_use_customer_directory);
  const canEdit = Boolean(user?.can_edit_customers);
  const companiesTab = search.tab === "companies";

  const [query, setQuery] = useState(search.q ?? "");
  const debouncedQuery = useDebounced(query.trim(), 250);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [drawer, setDrawer] = useState<CustomerDrawerState | null>(null);

  const setSearch = (patch: Partial<CustomerDirectorySearch>, replace = false) =>
    void navigate({
      search: (prev: CustomerDirectorySearch) => ({ ...prev, ...patch }),
      replace,
    });

  // Typing writes the (debounced) term to the URL.
  useEffect(() => {
    if ((search.q ?? "") === debouncedQuery) return;
    setPageSize(PAGE_SIZE);
    setSearch({ q: debouncedQuery || undefined }, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- setSearch is a fresh closure each render
  }, [debouncedQuery, search.q]);

  const q = search.q ?? "";
  const valid = search.invalid ? ("all" as const) : ("valid" as const);

  const shortlistQ = useQuery({
    queryKey: ["customer-directory", "shortlist"],
    queryFn: ({ signal }) => api.getCustomerShortlist(signal),
    enabled: allowed,
    staleTime: 30 * 1000,
  });
  const peopleQ = useQuery({
    queryKey: ["customer-directory", "search", q, valid, pageSize],
    queryFn: ({ signal }) =>
      api.listCustomerDirectory({ search: q, valid, page: 1, pageSize }, signal),
    enabled: allowed && !companiesTab && q !== "",
    placeholderData: keepPreviousData,
  });
  const companiesQ = useQuery({
    queryKey: ["customer-directory", "companies", q],
    queryFn: ({ signal }) => api.searchCustomerDirectoryCompanies(q, signal),
    enabled: allowed && companiesTab && q !== "",
  });

  const recent = useMemo(() => shortlistQ.data?.recent ?? [], [shortlistQ.data]);
  const frequent = useMemo(() => shortlistQ.data?.frequent ?? [], [shortlistQ.data]);
  // Companies of the agent's own customers, for the companies tab without a term.
  const ownCompanies = useMemo(() => {
    const seen = new Map<string, string>();
    for (const e of [...recent, ...frequent]) {
      if (e.company_name && !seen.has(e.customer_id)) seen.set(e.customer_id, e.company_name);
    }
    return Array.from(seen, ([customer_id, name]) => ({ customer_id, name }));
  }, [recent, frequent]);

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

  const selectPerson = (login: string) => setSearch({ tab: undefined, sel: login });
  const selectCompany = (customerId: string) =>
    setSearch({ tab: "companies", company: customerId });
  const people = peopleQ.data?.items ?? [];
  const peopleTotal = peopleQ.data?.total ?? 0;
  const companies = companiesQ.data ?? [];

  const pickFirst = () => {
    if (companiesTab) {
      const first = (q ? companies : ownCompanies)[0];
      if (first) selectCompany(first.customer_id);
    } else {
      const first = q ? people[0]?.login : recent[0]?.login;
      if (first) selectPerson(first);
    }
  };

  return (
    <div className="flex min-h-full flex-col" data-testid="customer-directory-page">
      <CustomerCallStrip onShowCustomer={selectPerson} />
      <div className="flex flex-1 flex-wrap">
        <aside
          className="flex w-full flex-col gap-3 border-hairline bg-surface/40 px-3 py-4 lg:sticky lg:top-0 lg:max-h-[calc(100vh-3rem)] lg:w-[340px] lg:border-r"
          aria-label={t("customerDirectory.title")}
        >
          <div
            role="tablist"
            aria-label={t("customerWorkbench.scope")}
            className="flex gap-0.5 rounded-lg border border-hairline bg-bg p-0.5"
          >
            <button
              type="button"
              role="tab"
              aria-selected={!companiesTab}
              className={tabClass(!companiesTab)}
              onClick={() => setSearch({ tab: undefined })}
              data-testid="customer-tab-people"
            >
              {t("customerWorkbench.people")}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={companiesTab}
              className={tabClass(companiesTab)}
              onClick={() => setSearch({ tab: "companies" })}
              data-testid="customer-tab-companies"
            >
              {t("customerWorkbench.companies")}
            </button>
          </div>

          <label className="relative block">
            <span className="sr-only">{t("customerWorkbench.searchLabel")}</span>
            <SearchIcon className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[16px] text-muted" />
            <input
              type="search"
              value={query}
              autoFocus
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  pickFirst();
                }
              }}
              placeholder={
                companiesTab
                  ? t("customerWorkbench.searchCompanies")
                  : t("customerWorkbench.searchPeople")
              }
              data-testid="customer-directory-search"
              className="w-full rounded-lg border border-hairline bg-surface py-2.5 pl-9 pr-3 text-[14px] text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
            />
          </label>

          <div className="-mx-1 min-h-0 flex-1 overflow-y-auto" data-testid="customer-rail-list">
            {!companiesTab && !q && (
              <ShortlistSections
                recent={recent}
                frequent={frequent}
                loading={shortlistQ.isLoading}
                selected={search.sel}
                onSelect={selectPerson}
                when={(iso) => shortWhen(iso, i18n.language)}
              />
            )}

            {!companiesTab && q && (
              <>
                <RailHeading>
                  {peopleQ.isLoading ? "…" : t("customerWorkbench.hits", { count: peopleTotal })}
                </RailHeading>
                {people.map((p) => (
                  <PersonRow
                    key={p.login}
                    name={customerName(p)}
                    email={p.email}
                    sub={[p.company_name, p.email].filter(Boolean).join(", ")}
                    invalid={p.valid_id !== 1}
                    selected={search.sel === p.login}
                    onSelect={() => selectPerson(p.login)}
                    testId={`customer-row-${p.login}`}
                  />
                ))}
                {!peopleQ.isLoading && people.length === 0 && (
                  <EmptyRail>
                    {t("customerWorkbench.noHits")}
                    {canEdit && (
                      <button
                        type="button"
                        className="mt-2 block font-medium text-accent hover:underline"
                        onClick={() => setDrawer({ mode: "create", prefill: prefillFrom(q) })}
                        data-testid="customer-create-from-search"
                      >
                        {t("customerWorkbench.createFromSearch", { term: q })}
                      </button>
                    )}
                  </EmptyRail>
                )}
                {people.length < peopleTotal && (
                  <button
                    type="button"
                    className="mx-2 mt-1 text-sm text-accent hover:underline"
                    onClick={() => setPageSize((n) => n + PAGE_SIZE)}
                    data-testid="customer-more"
                  >
                    {peopleQ.isFetching ? <Spinner className="h-3.5 w-3.5" /> : t("customerWorkbench.more")}
                  </button>
                )}
              </>
            )}

            {companiesTab && (
              <>
                <RailHeading>
                  {q
                    ? companiesQ.isLoading
                      ? "…"
                      : t("customerWorkbench.hits", { count: companies.length })
                    : t("customerWorkbench.ownCompanies")}
                </RailHeading>
                {(q ? companies : ownCompanies).map((co) => (
                  <CompanyRow
                    key={co.customer_id}
                    name={co.name}
                    customerId={co.customer_id}
                    selected={search.company === co.customer_id}
                    onSelect={() => selectCompany(co.customer_id)}
                  />
                ))}
                {q && !companiesQ.isLoading && companies.length === 0 && (
                  <EmptyRail>{t("customerDirectory.noCompanyMatch")}</EmptyRail>
                )}
                {!q && ownCompanies.length === 0 && (
                  <EmptyRail>{t("customerWorkbench.searchCompaniesHint")}</EmptyRail>
                )}
              </>
            )}
          </div>

          <div className="space-y-2 border-t border-hairline pt-3">
            {canEdit && (
              <button
                type="button"
                onClick={() => setDrawer({ mode: "create" })}
                className="flex w-full items-center justify-center gap-1.5 rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-subtle"
                data-testid="customer-new"
              >
                <PlusIcon className="text-[14px]" />
                {t("customerWorkbench.newCustomer")}
              </button>
            )}
            <div className="flex flex-wrap items-center justify-between gap-2 px-1 text-xs text-muted">
              <label className="inline-flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={Boolean(search.invalid)}
                  onChange={(e) => setSearch({ invalid: e.target.checked || undefined })}
                  data-testid="customer-directory-invalid"
                />
                {t("customerDirectory.includeInvalid")}
              </label>
              {!companiesTab && q && peopleTotal > 0 && peopleTotal <= VCARD_EXPORT_MAX && (
                <a
                  href={api.customerDirectoryVcardsUrl({ search: q, valid })}
                  download
                  className="inline-flex items-center gap-1 text-accent hover:underline"
                  data-testid="customer-directory-export-list"
                >
                  <DownloadIcon className="text-[13px]" />
                  {t("customerWorkbench.exportHits", { count: peopleTotal })}
                </a>
              )}
            </div>
          </div>
        </aside>

        <section className="min-w-0 flex-[999_1_460px] px-6 py-5" aria-live="polite">
          {companiesTab ? (
            search.company ? (
              <CompanyPanel
                key={search.company}
                customerId={search.company}
                onSelectContact={selectPerson}
                onAddContact={(company) => setDrawer({ mode: "create", company })}
              />
            ) : (
              <EmptyPane>{t("customerWorkbench.pickCompany")}</EmptyPane>
            )
          ) : search.sel ? (
            <CustomerPanel
              key={search.sel}
              login={search.sel}
              onEdit={(customer) => setDrawer({ mode: "edit", customer })}
              onOpenCompany={selectCompany}
            />
          ) : (
            <EmptyPane>{t("customerWorkbench.pickCustomer")}</EmptyPane>
          )}
        </section>
      </div>

      {drawer && (
        <CustomerEditDrawer
          state={drawer}
          onClose={() => setDrawer(null)}
          onSaved={(login) => {
            setDrawer(null);
            selectPerson(login);
          }}
        />
      )}
    </div>
  );
}

/** A typed search term as create-form prefill: an address, a number or a name. */
function prefillFrom(term: string) {
  const v = term.trim();
  if (v.includes("@")) return { email: v };
  if (/^[+\d][\d\s/()-]{4,}$/.test(v)) return { phone: v };
  const parts = v.split(/\s+/);
  return parts.length > 1
    ? { first_name: parts.slice(0, -1).join(" "), last_name: parts[parts.length - 1] }
    : { last_name: v };
}

function ShortlistSections({
  recent,
  frequent,
  loading,
  selected,
  onSelect,
  when,
}: {
  recent: CustomerShortlistEntry[];
  frequent: CustomerShortlistEntry[];
  loading: boolean;
  selected?: string;
  onSelect: (login: string) => void;
  when: (iso: string) => string;
}) {
  const { t } = useTranslation();
  const channel = (name: string | null | undefined) =>
    name === "Email"
      ? t("customerWorkbench.viaEmail")
      : name === "Phone"
        ? t("customerWorkbench.viaPhone")
        : name === "Internal"
          ? t("customerWorkbench.viaNote")
          : t("customerWorkbench.viaOther");
  if (loading) {
    return (
      <div className="flex justify-center py-6">
        <Spinner className="h-4 w-4" />
      </div>
    );
  }
  if (recent.length === 0 && frequent.length === 0) {
    return <EmptyRail>{t("customerWorkbench.shortlistEmpty")}</EmptyRail>;
  }
  return (
    <>
      {recent.length > 0 && <RailHeading>{t("customerWorkbench.recent")}</RailHeading>}
      {recent.map((e) => (
        <PersonRow
          key={`r-${e.login}`}
          name={customerName(e)}
          email={e.email}
          sub={[e.company_name, channel(e.last_channel)].filter(Boolean).join(", ")}
          meta={when(e.last_at)}
          selected={selected === e.login}
          onSelect={() => onSelect(e.login)}
          testId={`customer-recent-${e.login}`}
        />
      ))}
      {frequent.length > 0 && <RailHeading>{t("customerWorkbench.frequent")}</RailHeading>}
      {frequent.map((e) => (
        <PersonRow
          key={`f-${e.login}`}
          name={customerName(e)}
          email={e.email}
          sub={e.company_name || e.email}
          meta={t("customerWorkbench.ticketCount", { count: e.ticket_count })}
          selected={selected === e.login}
          onSelect={() => onSelect(e.login)}
          testId={`customer-frequent-${e.login}`}
        />
      ))}
    </>
  );
}

function RailHeading({ children }: { children: ReactNode }) {
  return <h3 className="mx-2 mb-1 mt-3 text-xs font-semibold text-muted first:mt-1">{children}</h3>;
}

function EmptyRail({ children }: { children: ReactNode }) {
  return <div className="px-2 py-4 text-sm text-muted">{children}</div>;
}

function EmptyPane({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-[16rem] place-items-center rounded-lg border border-dashed border-hairline p-8 text-center text-sm text-muted">
      {children}
    </div>
  );
}

const rowClass = (selected: boolean) =>
  cn(
    "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
    selected ? "bg-accent-dim shadow-[inset_3px_0_0_var(--color-accent)]" : "hover:bg-surface-subtle",
  );

function PersonRow({
  name,
  email,
  sub,
  meta,
  invalid,
  selected,
  onSelect,
  testId,
}: {
  name: string;
  email?: string | null;
  sub: string;
  meta?: string;
  invalid?: boolean;
  selected: boolean;
  onSelect: () => void;
  testId: string;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected || undefined}
      className={rowClass(selected)}
      data-testid={testId}
    >
      <Avatar email={email} initials={initialsOf(name)} size={30} />
      <span className="min-w-0 flex-1">
        <span className={cn("block truncate font-medium", invalid ? "text-muted line-through" : "text-ink")}>
          {name}
          {invalid && <span className="sr-only"> ({t("customerWorkbench.invalid")})</span>}
        </span>
        <span className="block truncate text-xs text-muted">{sub}</span>
      </span>
      {meta && <span className="shrink-0 text-[11px] tabular-nums text-muted">{meta}</span>}
    </button>
  );
}

function CompanyRow({
  name,
  customerId,
  selected,
  onSelect,
}: {
  name: string;
  customerId: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected || undefined}
      className={rowClass(selected)}
      data-testid={`company-row-${customerId}`}
    >
      <span className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-md border border-hairline bg-surface-subtle text-muted">
        <BuildingIcon className="text-[15px]" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-ink">{name}</span>
        <span className="block truncate font-mono text-[11px] text-muted">{customerId}</span>
      </span>
    </button>
  );
}
