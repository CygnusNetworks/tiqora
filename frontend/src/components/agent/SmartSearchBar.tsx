import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { SearchIcon } from "@/components/ui/icons";
import {
  FILTER_KEY_HINTS,
  formatCustomerLabel,
  isFilterComposition,
  matchQueues,
  parseKeyed,
  uniqueQueueMatch,
  type AgentOption,
  type QueueOption,
  type SmartPatch,
  type SmartSearchValues,
} from "@/components/agent/smartSearch";

/**
 * Smart, token-aware search field. Free text drives the full-text query; typing
 * a ``key:value`` prefix (queue:, besitzer:, status:, kunde:, von:, bis: — plus
 * English aliases) recognises a structured filter and, once picked, renders it
 * as a colour-coded, removable chip inside the field. Every chip maps onto the
 * same URL search params the classic filter panel below writes, so the two stay
 * in sync automatically. This component owns no filter state of its own — it is
 * a view over the caller's values plus a set of patch callbacks.
 */

type ChipKind = "queue" | "status" | "owner" | "customer" | "date";

const CHIP_CLASS: Record<ChipKind, string> = {
  queue: "text-teal-500 bg-teal-500/10 border-teal-500/30",
  status: "text-amber-500 bg-amber-500/10 border-amber-500/30",
  owner: "text-violet-500 bg-violet-500/10 border-violet-500/30",
  customer: "text-pink-500 bg-pink-500/10 border-pink-500/30",
  date: "text-emerald-500 bg-emerald-500/10 border-emerald-500/30",
};

const STATE_TYPES = ["new", "open", "pending", "closed"] as const;

/** Accept ``YYYY-MM-DD`` or ``DD.MM.YYYY``; return ISO ``YYYY-MM-DD`` or null. */
function parseDate(frag: string): string | null {
  const s = frag.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  const m = s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
  if (m) {
    const [, d, mo, y] = m;
    return `${y}-${mo.padStart(2, "0")}-${d.padStart(2, "0")}`;
  }
  return null;
}

type Suggestion = {
  id: string;
  kind: ChipKind | "text" | "hint";
  label: string;
  hint?: string;
  /** When false, row is informational only (no apply on Enter). */
  actionable?: boolean;
  apply: () => void;
};

export function SmartSearchBar({
  values,
  queues,
  agents,
  onPatch,
  onSubmitQuery,
  onQueryChange,
  inputTestId = "search-input",
  submitLabel,
  autoFocus = false,
  compact = false,
  onEscape,
  freeTextSuggest = true,
  onComposingFilterChange,
}: {
  values: SmartSearchValues;
  queues: QueueOption[];
  agents: AgentOption[];
  onPatch: (patch: SmartPatch) => void;
  onSubmitQuery: (term: string) => void;
  /** Fires on every free-text keystroke (NOT while composing a key:token), so
   * consumers can drive live results as the user types. */
  onQueryChange?: (text: string) => void;
  /** data-testid for the free-text input (default "search-input"). */
  inputTestId?: string;
  /** Override the submit button label; when null the button is hidden. */
  submitLabel?: string | null;
  /** Focus the free-text input on mount. */
  autoFocus?: boolean;
  /** Tighter chrome for the header command palette. */
  compact?: boolean;
  /** Called when Escape is pressed with an empty input (e.g. close the palette). */
  onEscape?: () => void;
  /** When false, free-text key-prefix / “search as fulltext” rows are omitted
   * (header palette shows ticket hits instead). Keyed filter typeahead stays. */
  freeTextSuggest?: boolean;
  /** Notifies parent when the user is composing a filter token (hide ticket hits). */
  onComposingFilterChange?: (composing: boolean) => void;
}) {
  const { t } = useTranslation();
  // The input mirrors the active free-text query; while composing a key:token it
  // holds that transient text instead. It re-syncs whenever the URL query changes.
  const [text, setText] = useState(values.q);
  const [active, setActive] = useState(0);
  const [focused, setFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Keep latest values/onQueryChange for commit without stale closures in suggestions.
  const valuesRef = useRef(values);
  valuesRef.current = values;
  const onQueryChangeRef = useRef(onQueryChange);
  onQueryChangeRef.current = onQueryChange;

  useEffect(() => {
    // Only re-sync free text when not mid-filter-token (avoids wiping "queue:…").
    if (!isFilterComposition(text)) setText(values.q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values.q]);

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  const composingFilter = isFilterComposition(text);
  useEffect(() => {
    onComposingFilterChange?.(composingFilter);
  }, [composingFilter, onComposingFilterChange]);

  const queueName = (id: number) => queues.find((q) => q.id === id)?.name ?? String(id);
  const agentName = (id: number) =>
    agents.find((a) => a.id === id)?.full_name ?? String(id);

  // --- Active chips derived from the current filter values ---------------
  const chips = useMemo(() => {
    const out: { key: string; kind: ChipKind; label: string; remove: () => void }[] = [];
    for (const id of values.queueIds)
      out.push({
        key: `q${id}`,
        kind: "queue",
        label: queueName(id),
        remove: () => onPatch({ queue_id: values.queueIds.filter((x) => x !== id) }),
      });
    for (const st of values.stateTypes)
      out.push({
        key: `s${st}`,
        kind: "status",
        label: t(`search.filters.state.${st}`),
        remove: () => onPatch({ state_type: values.stateTypes.filter((x) => x !== st) }),
      });
    if (values.ownerId != null)
      out.push({
        key: "owner",
        kind: "owner",
        label: agentName(values.ownerId),
        remove: () => onPatch({ owner_id: undefined }),
      });
    if (values.customerId)
      out.push({
        key: "customer",
        kind: "customer",
        label: formatCustomerLabel(values.customerLabel || values.customerId, values.customerId),
        remove: () => onPatch({ customer_id: undefined, customer_label: undefined }),
      });
    if (values.createdFrom)
      out.push({
        key: "from",
        kind: "date",
        label: `${t("search.smart.from")}: ${values.createdFrom}`,
        remove: () => onPatch({ created_from: undefined }),
      });
    if (values.createdTo)
      out.push({
        key: "to",
        kind: "date",
        label: `${t("search.smart.to")}: ${values.createdTo}`,
        remove: () => onPatch({ created_to: undefined }),
      });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values, queues, agents, t]);

  // --- Customer typeahead (only while typing a kunde:/customer: fragment) --
  const keyed = parseKeyed(text);
  const customerFrag = keyed?.key === "customer" ? keyed.frag.trim() : "";
  const customerQ = useQuery({
    queryKey: ["reference", "customer-search", customerFrag],
    queryFn: ({ signal }) => api.customerQuickSearch({ q: customerFrag, limit: 8 }, signal),
    enabled: customerFrag.length >= 2,
  });

  /** Apply a filter chip and clear the token input (never restore polluted free text). */
  const commit = (patch: SmartPatch) => {
    onPatch(patch);
    setText("");
    setActive(0);
    // Drop any partial-key that leaked into free-text before the colon.
    const q = valuesRef.current.q;
    if (q && isFilterComposition(q)) onQueryChangeRef.current?.("");
    inputRef.current?.focus();
  };

  // --- Build the suggestion list for the current input -------------------
  const suggestions: Suggestion[] = useMemo(() => {
    const raw = text.trimEnd();
    if (!raw.trim()) return [];
    const parsed = parseKeyed(raw);

    if (parsed) {
      const frag = parsed.frag.replace(/\s+$/, "");
      const fragLow = frag.toLowerCase().trim();

      if (parsed.key === "queue") {
        const matched = matchQueues(queues, fragLow, values.queueIds, 8);
        if (matched.length === 0) {
          return [
            {
              id: "queue-empty",
              kind: "hint" as const,
              label: t("search.smart.noQueueMatch", { frag: fragLow || "…" }),
              actionable: false,
              apply: () => {},
            },
          ];
        }
        return matched.map((qu) => ({
          id: `queue-${qu.id}`,
          kind: "queue" as const,
          label: qu.name,
          apply: () => commit({ queue_id: [...values.queueIds, qu.id] }),
        }));
      }
      if (parsed.key === "owner") {
        const matched = agents
          .filter(
            (a) =>
              !fragLow ||
              a.full_name.toLowerCase().includes(fragLow) ||
              a.login.toLowerCase().includes(fragLow),
          )
          .slice(0, 6);
        if (matched.length === 0) {
          return [
            {
              id: "owner-empty",
              kind: "hint" as const,
              label: t("search.smart.noOwnerMatch", { frag: fragLow || "…" }),
              actionable: false,
              apply: () => {},
            },
          ];
        }
        return matched.map((a) => ({
          id: `owner-${a.id}`,
          kind: "owner" as const,
          label: a.full_name,
          hint: a.login,
          apply: () => commit({ owner_id: a.id }),
        }));
      }
      if (parsed.key === "status") {
        const matched = STATE_TYPES.filter(
          (st) =>
            !fragLow ||
            st.includes(fragLow) ||
            t(`search.filters.state.${st}`).toLowerCase().includes(fragLow),
        ).filter((st) => !values.stateTypes.includes(st));
        return matched.map((st) => ({
          id: `status-${st}`,
          kind: "status" as const,
          label: t(`search.filters.state.${st}`),
          apply: () => commit({ state_type: [...values.stateTypes, st] }),
        }));
      }
      if (parsed.key === "customer") {
        if (fragLow.length < 2) {
          return [
            {
              id: "customer-hint",
              kind: "hint" as const,
              label: t("search.smart.customerHint"),
              actionable: false,
              apply: () => {},
            },
          ];
        }
        if (customerQ.isLoading || customerQ.isFetching) {
          return [
            {
              id: "customer-loading",
              kind: "hint" as const,
              label: t("search.smart.loading"),
              actionable: false,
              apply: () => {},
            },
          ];
        }
        const items: Suggestion[] = [];
        for (const c of customerQ.data?.companies ?? [])
          items.push({
            id: `co-${c.customer_id}`,
            kind: "customer",
            label: c.name,
            hint: c.customer_id,
            apply: () =>
              commit({
                customer_id: c.customer_id,
                customer_label: formatCustomerLabel(c.name, c.customer_id),
              }),
          });
        for (const c of customerQ.data?.contacts ?? []) {
          const name = `${c.first_name} ${c.last_name}`.trim() || c.login || c.customer_id;
          const label = c.company_name ? `${name} · ${c.company_name}` : name;
          items.push({
            id: `ct-${c.login}`,
            kind: "customer",
            label,
            hint: c.customer_id || c.email,
            apply: () =>
              commit({
                customer_id: c.customer_id,
                customer_label: formatCustomerLabel(label, c.customer_id),
              }),
          });
        }
        if (items.length === 0) {
          return [
            {
              id: "customer-empty",
              kind: "hint" as const,
              label: t("search.smart.noCustomerMatch", { frag: fragLow }),
              actionable: false,
              apply: () => {},
            },
          ];
        }
        return items.slice(0, 8);
      }
      // from / to → date
      const iso = parseDate(parsed.frag);
      if (iso) {
        const patch = parsed.key === "from" ? { created_from: iso } : { created_to: iso };
        return [
          {
            id: `date-${parsed.key}`,
            kind: "date" as const,
            label: `${t(parsed.key === "from" ? "search.smart.from" : "search.smart.to")}: ${iso}`,
            apply: () => commit(patch),
          },
        ];
      }
      return [
        {
          id: "date-hint",
          kind: "hint" as const,
          label: t("search.smart.dateHint"),
          actionable: false,
          apply: () => {},
        },
      ];
    }

    // Not a recognised key yet: offer matching filter keys, plus full-text.
    const low = raw.trim().toLowerCase();
    const keyHints: Suggestion[] = FILTER_KEY_HINTS.filter((k) => k.startsWith(low)).map((k) => ({
      id: `key-${k}`,
      kind: "text" as const,
      label: `${k}:`,
      hint: t("search.smart.filterHint"),
      apply: () => {
        setText(`${k}:`);
        setActive(0);
        inputRef.current?.focus();
      },
    }));
    if (!freeTextSuggest) return keyHints;
    return [
      ...keyHints,
      {
        id: "fulltext",
        kind: "text" as const,
        label: t("search.smart.fulltext", { text: raw.trim() }),
        hint: "Enter",
        apply: () => {
          onSubmitQuery(raw.trim());
          setFocused(false);
        },
      },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, queues, agents, values, customerQ.data, customerQ.isLoading, customerQ.isFetching, t, freeTextSuggest]);

  /** Try to auto-commit a unique filter match from the current token. */
  const tryAutoCommit = (raw: string): boolean => {
    const parsed = parseKeyed(raw.trimEnd());
    if (!parsed) return false;
    const frag = parsed.frag.replace(/\s+$/, "").trim();

    if (parsed.key === "queue") {
      const hit = uniqueQueueMatch(queues, frag, values.queueIds);
      if (hit) {
        commit({ queue_id: [...values.queueIds, hit.id] });
        return true;
      }
    }
    if (parsed.key === "owner" && frag) {
      const matched = agents.filter(
        (a) =>
          a.full_name.toLowerCase().includes(frag.toLowerCase()) ||
          a.login.toLowerCase().includes(frag.toLowerCase()),
      );
      const exact = matched.find(
        (a) =>
          a.full_name.toLowerCase() === frag.toLowerCase() ||
          a.login.toLowerCase() === frag.toLowerCase(),
      );
      const hit = exact ?? (matched.length === 1 ? matched[0] : null);
      if (hit) {
        commit({ owner_id: hit.id });
        return true;
      }
    }
    if (parsed.key === "status" && frag) {
      const matched = STATE_TYPES.filter(
        (st) =>
          st.includes(frag.toLowerCase()) ||
          t(`search.filters.state.${st}`).toLowerCase().includes(frag.toLowerCase()),
      ).filter((st) => !values.stateTypes.includes(st));
      if (matched.length === 1) {
        commit({ state_type: [...values.stateTypes, matched[0]!] });
        return true;
      }
    }
    if ((parsed.key === "from" || parsed.key === "to") && frag) {
      const iso = parseDate(frag);
      if (iso) {
        commit(parsed.key === "from" ? { created_from: iso } : { created_to: iso });
        return true;
      }
    }
    // Customer: only auto-commit when exactly one result is loaded.
    if (parsed.key === "customer" && frag.length >= 2 && customerQ.data) {
      const companies = customerQ.data.companies ?? [];
      const contacts = customerQ.data.contacts ?? [];
      if (companies.length === 1 && contacts.length === 0) {
        const c = companies[0]!;
        commit({
          customer_id: c.customer_id,
          customer_label: formatCustomerLabel(c.name, c.customer_id),
        });
        return true;
      }
      if (contacts.length === 1 && companies.length === 0) {
        const c = contacts[0]!;
        const name = `${c.first_name} ${c.last_name}`.trim() || c.login || c.customer_id;
        const label = c.company_name ? `${name} · ${c.company_name}` : name;
        commit({
          customer_id: c.customer_id,
          customer_label: formatCustomerLabel(label, c.customer_id),
        });
        return true;
      }
    }
    return false;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    const actionable = suggestions.filter((s) => s.actionable !== false);
    if (e.key === "ArrowDown" && actionable.length) {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp" && actionable.length) {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const chosen = suggestions[active];
      if (chosen && chosen.actionable !== false) {
        chosen.apply();
        return;
      }
      // Prefer unique auto-commit for filter tokens over dumping raw "queue:…" as q.
      if (parseKeyed(text) && tryAutoCommit(text)) return;
      if (parseKeyed(text)) return; // no match — don't pollute free text
      const firstActionable = actionable[0];
      if (firstActionable) {
        firstActionable.apply();
        return;
      }
      onSubmitQuery(text.trim());
      setFocused(false);
    } else if (e.key === "Backspace" && text === "" && chips.length) {
      chips[chips.length - 1]!.remove();
    } else if (e.key === "Escape") {
      if (text !== "") {
        setText("");
        if (!isFilterComposition(text)) onQueryChange?.("");
        else if (values.q && isFilterComposition(values.q)) onQueryChange?.("");
        return;
      }
      onEscape?.();
    }
  };

  const handleChange = (val: string) => {
    // Trailing space on a filter token → try unique auto-commit.
    if (/\s$/.test(val) && parseKeyed(val.trimEnd())) {
      if (tryAutoCommit(val)) return;
      // Ambiguous: drop the space, keep the token, show suggestions.
      setText(val.trimEnd());
      setActive(0);
      return;
    }
    setText(val);
    setActive(0);
    if (onQueryChange && !isFilterComposition(val)) onQueryChange(val);
  };

  const showSuggest = focused && text.trim().length > 0 && suggestions.length > 0;

  // Keep active index in range when list shrinks.
  const safeActive = Math.min(active, Math.max(0, suggestions.length - 1));

  return (
    <div className="flex gap-2">
      <div className="relative flex-1">
        <div
          className={
            compact
              ? "flex min-h-9 flex-wrap items-center gap-1 rounded-lg border border-hairline bg-surface-subtle px-2 py-1 focus-within:border-accent focus-within:outline focus-within:outline-2 focus-within:outline-offset-1 focus-within:outline-accent"
              : "flex min-h-[42px] flex-wrap items-center gap-1.5 rounded-md border border-hairline bg-surface px-2.5 py-1.5 focus-within:border-accent focus-within:outline focus-within:outline-2 focus-within:outline-offset-1 focus-within:outline-accent"
          }
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) inputRef.current?.focus();
          }}
          data-testid="smart-search-field"
        >
          <SearchIcon className={`shrink-0 text-muted ${compact ? "text-[15px]" : "text-[16px]"}`} />
          {chips.map((chip) => (
            <span
              key={chip.key}
              className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs ${CHIP_CLASS[chip.kind]}`}
              data-testid={`smart-chip-${chip.key}`}
            >
              <span className="max-w-[16rem] truncate">{chip.label}</span>
              <button
                type="button"
                aria-label={t("search.filters.clear")}
                className="opacity-60 hover:opacity-100"
                onMouseDown={(e) => {
                  e.preventDefault();
                  chip.remove();
                }}
                data-testid={`smart-chip-remove-${chip.key}`}
              >
                ×
              </button>
            </span>
          ))}
          <input
            ref={inputRef}
            value={text}
            onChange={(e) => handleChange(e.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            onKeyDown={onKeyDown}
            placeholder={chips.length ? "" : t("search.smart.placeholder")}
            data-testid={inputTestId}
            autoComplete="off"
            spellCheck={false}
            className={`min-w-[8rem] flex-1 bg-transparent text-ink placeholder:text-muted focus:outline-none ${
              compact ? "py-0.5 text-[13px]" : "py-0.5 text-sm"
            }`}
          />
        </div>

        {showSuggest && (
          <ul
            className="absolute z-50 mt-1 max-h-[min(16rem,50vh)] w-full overflow-auto rounded-lg border border-hairline bg-surface py-1 shadow-lg"
            data-testid="smart-search-suggest"
          >
            {suggestions.map((s, i) => {
              const isHint = s.kind === "hint" || s.actionable === false;
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    disabled={isHint}
                    onMouseDown={(e) => {
                      if (isHint) return;
                      e.preventDefault();
                      s.apply();
                    }}
                    onMouseEnter={() => !isHint && setActive(i)}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm ${
                      isHint
                        ? "cursor-default text-muted"
                        : i === safeActive
                          ? "bg-surface-subtle text-ink"
                          : "text-ink"
                    }`}
                    data-testid={`smart-suggest-${s.id}`}
                  >
                    {s.kind !== "text" && s.kind !== "hint" && (
                      <span
                        className={`h-2 w-2 shrink-0 rounded-full ${CHIP_CLASS[s.kind as ChipKind]}`}
                        aria-hidden
                      />
                    )}
                    <span className="min-w-0 flex-1 truncate">{s.label}</span>
                    {s.hint && <span className="shrink-0 text-xs text-muted">{s.hint}</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {submitLabel !== null && (
        <button
          type="button"
          onClick={() => onSubmitQuery(text.trim() || values.q)}
          className="shrink-0 rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-ink transition-colors duration-100 hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          data-testid="search-submit"
        >
          {submitLabel ?? t("search.submit")}
        </button>
      )}
    </div>
  );
}

