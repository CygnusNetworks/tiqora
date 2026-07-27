import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import DOMPurify from "dompurify";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { KbSearchHit } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { PriorityChip, StateChip } from "@/components/ui/StatusChip";
import { SmartSearchBar } from "@/components/agent/SmartSearchBar";

export type SearchSearch = {
  q?: string;
  offset?: number;
  /** Multi queue filter (repeated query keys). */
  queue_id?: number[];
  /** Multi state-type filter: new | open | pending | closed. */
  state_type?: string[];
  owner_id?: number;
  customer_id?: string;
  /** Display label for the active customer chip (not sent to the API). */
  customer_label?: string;
  /** ISO date YYYY-MM-DD */
  created_from?: string;
  /** ISO date YYYY-MM-DD */
  created_to?: string;
  /** Result ordering; defaults to changed_desc. */
  sort?: "changed_desc" | "created_desc" | "created_asc";
};

const STATE_TYPES = ["new", "open", "pending", "closed"] as const;
const SORT_ORDERS = ["changed_desc", "created_desc", "created_asc"] as const;
const DEFAULT_SORT = "changed_desc";

/** ISO date (YYYY-MM-DD) for N days before today, local calendar. */
function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Preset ranges for the quick chips. `days: null` = clear the range (all time). */
const RANGE_PRESETS: { key: string; days: number | null }[] = [
  { key: "all", days: null },
  { key: "today", days: 0 },
  { key: "d7", days: 7 },
  { key: "d30", days: 30 },
  { key: "d90", days: 90 },
];

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

/** Escape first, highlight query terms, then allow only em/mark through to the DOM. */
function highlight(text: string | null | undefined, q: string): string {
  if (!text) return "";
  // Never pass through raw HTML (attacker-controlled title/body can contain <em>).
  const escaped = escapeHtml(text);
  let withMarks = escaped;
  if (q.trim()) {
    const re = new RegExp(`(${escapeRegExp(q.trim())})`, "gi");
    withMarks = escaped.replace(re, "<mark>$1</mark>");
  }
  return DOMPurify.sanitize(withMarks, {
    ALLOWED_TAGS: ["em", "mark"],
    ALLOWED_ATTR: [],
  });
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function facetCount(
  facets: Record<string, Record<string, number>> | undefined,
  name: string,
  key: string | number,
): number | undefined {
  const dist = facets?.[name];
  if (!dist) return undefined;
  const n = dist[String(key)];
  return typeof n === "number" ? n : undefined;
}

export function SearchPage() {
  const { t } = useTranslation();
  const navigate = useNavigate({ from: "/agent/search" });
  const search = useSearch({ from: "/agent/search" }) as SearchSearch;
  const q = search.q ?? "";
  const offset = search.offset ?? 0;
  const queueIds = search.queue_id ?? [];
  const stateTypes = search.state_type ?? [];
  const ownerId = search.owner_id;
  const customerId = search.customer_id;
  const customerLabel = search.customer_label;
  const createdFrom = search.created_from;
  const createdTo = search.created_to;
  const sort = search.sort ?? DEFAULT_SORT;

  const [customerQuery, setCustomerQuery] = useState("");
  const debouncedCustomerQuery = useDebouncedValue(customerQuery, 250);

  const patchSearch = (patch: Partial<SearchSearch>) => {
    void navigate({
      search: (prev) => {
        const base = prev as SearchSearch;
        const next: SearchSearch = {
          ...base,
          ...patch,
          // Reset pagination when filters change unless offset is explicit.
          offset: patch.offset !== undefined ? patch.offset : 0,
        };
        // Drop empty arrays / blanks so the URL stays clean.
        if (next.queue_id && next.queue_id.length === 0) delete next.queue_id;
        if (next.state_type && next.state_type.length === 0) delete next.state_type;
        if (next.owner_id === undefined || next.owner_id === null) delete next.owner_id;
        if (!next.customer_id) {
          delete next.customer_id;
          delete next.customer_label;
        }
        if (!next.customer_label) delete next.customer_label;
        if (!next.created_from) delete next.created_from;
        if (!next.created_to) delete next.created_to;
        if (!next.sort || next.sort === DEFAULT_SORT) delete next.sort;
        if (!next.q) delete next.q;
        if (!next.offset) delete next.offset;
        return next;
      },
    });
  };

  const queuesQ = useQuery({
    queryKey: ["reference", "queues"],
    queryFn: ({ signal }) => api.listReferenceQueues({}, signal),
  });
  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: ({ signal }) => api.listReferenceAgents(signal),
  });
  const customerSearchQ = useQuery({
    queryKey: ["reference", "customer-search", debouncedCustomerQuery],
    queryFn: ({ signal }) =>
      api.customerQuickSearch({ q: debouncedCustomerQuery, limit: 10 }, signal),
    enabled: debouncedCustomerQuery.trim().length >= 2,
  });

  const resultsQ = useQuery({
    queryKey: [
      "search",
      q,
      offset,
      queueIds,
      stateTypes,
      ownerId,
      customerId,
      createdFrom,
      createdTo,
      sort,
    ],
    queryFn: ({ signal }) =>
      api.search(
        {
          q,
          offset,
          limit: 20,
          queue_id: queueIds.length ? queueIds : undefined,
          state_type: stateTypes.length ? stateTypes : undefined,
          owner_id: ownerId,
          customer_id: customerId,
          created_from: createdFrom,
          created_to: createdTo,
          sort,
        },
        signal,
      ),
    enabled: q.trim().length > 0,
  });

  const kbResultsQ = useQuery({
    queryKey: ["search", "kb", q],
    queryFn: () => api.searchKb({ q, limit: 20 }),
    enabled: q.trim().length > 0,
  });

  // KB search returns per-chunk hits; dedupe to one entry per article.
  const kbHits: KbSearchHit[] = [];
  const seenArticles = new Set<number>();
  for (const hit of kbResultsQ.data?.hits ?? []) {
    if (seenArticles.has(hit.article_id)) continue;
    seenArticles.add(hit.article_id);
    kbHits.push(hit);
  }

  const isLoading = resultsQ.isLoading || kbResultsQ.isLoading;
  const hasResults = Boolean(resultsQ.data) || Boolean(kbResultsQ.data);
  const totalHits = (resultsQ.data?.hits.length ?? 0) + kbHits.length;
  const facets = resultsQ.data?.facets;

  const agentItems: SelectMenuItem<number>[] = useMemo(
    () =>
      (agentsQ.data ?? []).map((a) => ({
        value: a.id,
        label: a.full_name,
        hint: a.login,
      })),
    [agentsQ.data],
  );

  const selectedOwnerLabel =
    agentsQ.data?.find((a) => a.id === ownerId)?.full_name ??
    (ownerId != null ? String(ownerId) : t("search.filters.ownerAll"));

  const hasActiveFilters =
    queueIds.length > 0 ||
    stateTypes.length > 0 ||
    ownerId != null ||
    Boolean(customerId) ||
    Boolean(createdFrom) ||
    Boolean(createdTo);

  const clearFilters = () => {
    setCustomerQuery("");
    patchSearch({
      queue_id: [],
      state_type: [],
      owner_id: undefined,
      customer_id: undefined,
      created_from: undefined,
      created_to: undefined,
      offset: 0,
    });
  };

  const toggleQueue = (id: number) => {
    const next = queueIds.includes(id)
      ? queueIds.filter((x) => x !== id)
      : [...queueIds, id];
    patchSearch({ queue_id: next });
  };

  const toggleStateType = (st: string) => {
    const next = stateTypes.includes(st)
      ? stateTypes.filter((x) => x !== st)
      : [...stateTypes, st];
    patchSearch({ state_type: next });
  };

  const applyRangePreset = (days: number | null) => {
    if (days === null) {
      patchSearch({ created_from: undefined, created_to: undefined });
    } else {
      patchSearch({ created_from: isoDaysAgo(days), created_to: isoDaysAgo(0) });
    }
  };

  // Which preset (if any) matches the active range. "all" when no range is set.
  const activeRangeKey =
    !createdFrom && !createdTo
      ? "all"
      : (RANGE_PRESETS.find((p) => p.days !== null && createdFrom === isoDaysAgo(p.days))?.key ??
        null);

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 px-4 py-6" data-testid="search-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("search.title")}</h1>

      <SmartSearchBar
        values={{
          q,
          queueIds,
          stateTypes,
          ownerId,
          customerId,
          customerLabel,
          createdFrom,
          createdTo,
        }}
        queues={(queuesQ.data ?? []).map((qu) => ({ id: qu.id, name: qu.name }))}
        agents={(agentsQ.data ?? []).map((a) => ({
          id: a.id,
          full_name: a.full_name,
          login: a.login,
        }))}
        onPatch={patchSearch}
        onSubmitQuery={(term) => patchSearch({ q: term, offset: 0 })}
      />

      {/* Quick time-range presets + result ordering. */}
      <div className="flex flex-wrap items-center gap-2" data-testid="search-controls">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">
          {t("search.presets.label")}
        </span>
        {RANGE_PRESETS.map((p) => {
          const active = activeRangeKey === p.key;
          return (
            <button
              key={p.key}
              type="button"
              onClick={() => applyRangePreset(p.days)}
              aria-pressed={active}
              data-testid={`search-preset-${p.key}`}
              className={
                active
                  ? "rounded-full border border-accent bg-accent-dim px-2.5 py-1 text-xs font-medium text-accent"
                  : "rounded-full border border-hairline bg-surface-subtle px-2.5 py-1 text-xs text-ink hover:border-accent/50"
              }
            >
              {t(`search.presets.${p.key}`)}
            </button>
          );
        })}
        <div className="ml-auto flex items-center gap-2">
          <label
            htmlFor="search-sort"
            className="text-[11px] font-semibold uppercase tracking-wide text-muted"
          >
            {t("search.sort.label")}
          </label>
          <select
            id="search-sort"
            value={sort}
            onChange={(e) =>
              patchSearch({ sort: e.target.value as SearchSearch["sort"] })
            }
            data-testid="search-sort"
            className="rounded-md border border-hairline bg-surface px-2.5 py-1 text-xs text-ink focus:border-accent focus:outline-none"
          >
            {SORT_ORDERS.map((o) => (
              <option key={o} value={o}>
                {t(`search.sort.${o}`)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div
        className="space-y-3 rounded-lg border border-hairline bg-surface p-3"
        data-testid="search-filters"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted">
            {t("search.filters.title")}
          </p>
          {hasActiveFilters && (
            <button
              type="button"
              onClick={clearFilters}
              className="text-xs text-accent hover:underline"
              data-testid="search-filter-clear"
            >
              {t("search.filters.clear")}
            </button>
          )}
        </div>

        {/* State-type chips */}
        <div className="flex flex-wrap gap-1.5" data-testid="search-filter-state-types">
          {STATE_TYPES.map((st) => {
            const active = stateTypes.includes(st);
            const count = facetCount(facets, "state_type", st);
            return (
              <button
                key={st}
                type="button"
                onClick={() => toggleStateType(st)}
                className={
                  active
                    ? "inline-flex items-center gap-1 rounded-full border border-accent bg-accent-dim px-2.5 py-1 text-xs font-medium text-accent"
                    : "inline-flex items-center gap-1 rounded-full border border-hairline bg-surface-subtle px-2.5 py-1 text-xs text-ink hover:border-accent/50"
                }
                data-testid={`search-filter-state-${st}`}
                aria-pressed={active}
              >
                {t(`search.filters.state.${st}`)}
                {count != null && (
                  <Badge tone={active ? "accent" : "muted"} data-testid={`search-facet-state-${st}`}>
                    {count}
                  </Badge>
                )}
              </button>
            );
          })}
        </div>

        {/* Queue multi-select as chips */}
        <div className="space-y-1" data-testid="search-filter-queues">
          <p className="text-[11px] font-medium text-muted">{t("search.filters.queue")}</p>
          <div className="flex flex-wrap gap-1.5">
            {(queuesQ.data ?? []).map((queue) => {
              const active = queueIds.includes(queue.id);
              const count = facetCount(facets, "queue_id", queue.id);
              return (
                <button
                  key={queue.id}
                  type="button"
                  onClick={() => toggleQueue(queue.id)}
                  className={
                    active
                      ? "inline-flex items-center gap-1 rounded-full border border-accent bg-accent-dim px-2.5 py-1 text-xs font-medium text-accent"
                      : "inline-flex items-center gap-1 rounded-full border border-hairline bg-surface-subtle px-2.5 py-1 text-xs text-ink hover:border-accent/50"
                  }
                  data-testid={`search-filter-queue-${queue.id}`}
                  aria-pressed={active}
                >
                  {queue.name}
                  {count != null && (
                    <Badge
                      tone={active ? "accent" : "muted"}
                      data-testid={`search-facet-queue-${queue.id}`}
                    >
                      {count}
                    </Badge>
                  )}
                </button>
              );
            })}
            {queuesQ.isLoading && <Spinner className="h-4 w-4" />}
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {/* Owner */}
          <div className="space-y-1" data-testid="search-filter-owner">
            <p className="text-[11px] font-medium text-muted">{t("search.filters.owner")}</p>
            <SelectMenu
              items={[
                { value: 0, label: t("search.filters.ownerAll") },
                ...agentItems,
              ]}
              value={ownerId ?? 0}
              onSelect={(v) =>
                patchSearch({ owner_id: v === 0 ? undefined : v })
              }
              panelTestId="search-filter-owner-panel"
              trigger={({ open, ref, toggleProps }) => (
                <button
                  type="button"
                  ref={ref}
                  {...toggleProps}
                  className="flex w-full items-center justify-between rounded-md border border-hairline bg-surface px-3 py-1.5 text-left text-sm text-ink hover:border-accent/50"
                  data-testid="search-filter-owner-trigger"
                  aria-expanded={open}
                >
                  <span className="truncate">{selectedOwnerLabel}</span>
                  <span className="text-muted" aria-hidden>
                    ▾
                  </span>
                </button>
              )}
            />
          </div>

          {/* Customer typeahead */}
          <div className="space-y-1" data-testid="search-filter-customer">
            <p className="text-[11px] font-medium text-muted">{t("search.filters.customer")}</p>
            {customerId ? (
              <div className="flex items-center gap-2 rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm">
                <span className="min-w-0 truncate font-medium text-ink" data-testid="search-filter-customer-value">
                  {customerLabel || customerId}
                </span>
                {customerLabel && customerLabel !== customerId && (
                  <span className="shrink-0 text-xs text-muted" data-testid="search-filter-customer-id">
                    {customerId}
                  </span>
                )}
                <button
                  type="button"
                  className="ml-auto shrink-0 text-xs text-accent hover:underline"
                  onClick={() => {
                    setCustomerQuery("");
                    patchSearch({ customer_id: undefined, customer_label: undefined });
                  }}
                  data-testid="search-filter-customer-clear"
                >
                  {t("search.filters.clear")}
                </button>
              </div>
            ) : (
              <div className="relative">
                <input
                  value={customerQuery}
                  onChange={(e) => setCustomerQuery(e.target.value)}
                  placeholder={t("search.filters.customerPlaceholder")}
                  className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                  data-testid="search-filter-customer-input"
                />
                {debouncedCustomerQuery.trim().length >= 2 &&
                  (customerSearchQ.data?.companies.length ||
                    customerSearchQ.data?.contacts.length) ? (
                  <ul
                    className="absolute z-10 mt-1 max-h-48 w-full overflow-auto rounded-md border border-hairline bg-surface shadow-md"
                    data-testid="search-filter-customer-results"
                  >
                    {(customerSearchQ.data?.companies ?? []).map((c) => (
                      <li key={`co-${c.customer_id}`}>
                        <button
                          type="button"
                          className="flex w-full flex-col px-3 py-1.5 text-left text-sm hover:bg-surface-subtle"
                          onClick={() => {
                            setCustomerQuery("");
                            patchSearch({
                              customer_id: c.customer_id,
                              customer_label: c.name,
                            });
                          }}
                          data-testid={`search-filter-customer-company-${c.customer_id}`}
                        >
                          <span className="font-medium text-ink">{c.name}</span>
                          <span className="text-xs text-muted">{c.customer_id}</span>
                        </button>
                      </li>
                    ))}
                    {(customerSearchQ.data?.contacts ?? []).map((c) => {
                      const name = `${c.first_name} ${c.last_name}`.trim();
                      const label = c.company_name
                        ? `${name || c.login} · ${c.company_name}`
                        : name || c.login || c.customer_id;
                      return (
                        <li key={`ct-${c.login}`}>
                          <button
                            type="button"
                            className="flex w-full flex-col px-3 py-1.5 text-left text-sm hover:bg-surface-subtle"
                            onClick={() => {
                              setCustomerQuery("");
                              patchSearch({
                                customer_id: c.customer_id,
                                customer_label: label,
                              });
                            }}
                            data-testid={`search-filter-customer-contact-${c.login}`}
                          >
                            <span className="font-medium text-ink">
                              {c.first_name} {c.last_name}
                            </span>
                            <span className="text-xs text-muted">
                              {c.email}
                              {c.company_name ? ` · ${c.company_name}` : ""}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </div>
            )}
          </div>

          {/* Date range */}
          <div className="space-y-1" data-testid="search-filter-created-from">
            <label className="text-[11px] font-medium text-muted" htmlFor="search-created-from">
              {t("search.filters.createdFrom")}
            </label>
            <input
              id="search-created-from"
              type="date"
              value={createdFrom ?? ""}
              onChange={(e) =>
                patchSearch({ created_from: e.target.value || undefined })
              }
              className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
              data-testid="search-filter-created-from-input"
            />
          </div>
          <div className="space-y-1" data-testid="search-filter-created-to">
            <label className="text-[11px] font-medium text-muted" htmlFor="search-created-to">
              {t("search.filters.createdTo")}
            </label>
            <input
              id="search-created-to"
              type="date"
              value={createdTo ?? ""}
              onChange={(e) =>
                patchSearch({ created_to: e.target.value || undefined })
              }
              className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
              data-testid="search-filter-created-to-input"
            />
          </div>
        </div>
      </div>

      {!q.trim() && (
        <p className="text-sm text-muted">{t("search.hint")}</p>
      )}

      {q.trim() && isLoading && (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      )}

      {q.trim() && hasResults && (
        <div className="space-y-6" data-testid="search-results">
          <section className="space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("search.groupTickets")}
            </h2>
            {resultsQ.data && (
              <p className="text-xs text-muted" data-testid="search-total">
                {t("search.results", {
                  total: resultsQ.data.estimated_total,
                  query: resultsQ.data.query,
                })}
              </p>
            )}
            <ul className="space-y-2">
              {(resultsQ.data?.hits ?? []).map((hit) => (
                <li key={hit.id}>
                  <Link
                    to="/agent/tickets/$ticketId"
                    params={{ ticketId: String(hit.id) }}
                    className="block rounded-lg border border-hairline bg-surface p-3 transition-colors duration-100 hover:border-accent/60 hover:bg-surface-subtle"
                    data-testid={`search-hit-${hit.id}`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-accent">{hit.tn}</span>
                      <StateChip state={hit.state} />
                      <PriorityChip priority={hit.priority} />
                      {hit.queue_name && (
                        <span className="text-xs text-muted">{hit.queue_name}</span>
                      )}
                    </div>
                    <p
                      className="mt-1 text-sm font-medium text-ink"
                      dangerouslySetInnerHTML={{
                        __html: highlight(hit.title, q),
                      }}
                    />
                    {hit.excerpt && (
                      <p
                        className="mt-1 text-xs text-muted line-clamp-2 [&_em]:bg-escalation/30 [&_em]:not-italic [&_mark]:bg-escalation/30"
                        dangerouslySetInnerHTML={{
                          __html: highlight(hit.excerpt, q),
                        }}
                      />
                    )}
                  </Link>
                </li>
              ))}
              {resultsQ.data && resultsQ.data.hits.length === 0 && (
                <li className="py-4 text-center text-sm text-muted">
                  {t("search.noResults")}
                </li>
              )}
            </ul>
          </section>

          <section className="space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("search.groupKb")}
            </h2>
            <ul className="space-y-2" data-testid="search-kb-results">
              {kbHits.map((hit) => (
                <li key={hit.article_id}>
                  <Link
                    to="/agent/kb/$articleId"
                    params={{ articleId: String(hit.article_id) }}
                    className="block rounded-lg border border-hairline bg-surface p-3 transition-colors duration-100 hover:border-accent/60 hover:bg-surface-subtle"
                    data-testid={`search-kb-hit-${hit.article_id}`}
                  >
                    <p
                      className="text-sm font-medium text-ink"
                      dangerouslySetInnerHTML={{ __html: highlight(hit.title, q) }}
                    />
                    {hit.heading_path && (
                      <p className="text-xs text-muted">{hit.heading_path}</p>
                    )}
                    <p
                      className="mt-1 text-xs text-muted line-clamp-2"
                      dangerouslySetInnerHTML={{
                        __html: highlight(hit.content, q),
                      }}
                    />
                  </Link>
                </li>
              ))}
              {kbResultsQ.data && kbHits.length === 0 && (
                <li className="py-4 text-center text-sm text-muted">
                  {t("search.noResults")}
                </li>
              )}
            </ul>
          </section>

          {totalHits === 0 && resultsQ.data && kbResultsQ.data && (
            <p className="text-center text-sm text-muted">{t("search.noResults")}</p>
          )}
        </div>
      )}
    </div>
  );
}
