import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { SearchIcon } from "@/components/ui/icons";

/**
 * Smart, token-aware search field. Free text drives the full-text query; typing
 * a ``key:value`` prefix (queue:, besitzer:, status:, kunde:, von:, bis: — plus
 * English aliases) recognises a structured filter and, once picked, renders it
 * as a colour-coded, removable chip inside the field. Every chip maps onto the
 * same URL search params the classic filter panel below writes, so the two stay
 * in sync automatically. This component owns no filter state of its own — it is
 * a view over the caller's values plus a set of patch callbacks.
 */

export type QueueOption = { id: number; name: string };
export type AgentOption = { id: number; full_name: string; login: string };

export type SmartSearchValues = {
  q: string;
  queueIds: number[];
  stateTypes: string[];
  ownerId?: number;
  customerId?: string;
  customerLabel?: string;
  createdFrom?: string;
  createdTo?: string;
};

type ChipKind = "queue" | "status" | "owner" | "customer" | "date";

const CHIP_CLASS: Record<ChipKind, string> = {
  queue: "text-teal-500 bg-teal-500/10 border-teal-500/30",
  status: "text-amber-500 bg-amber-500/10 border-amber-500/30",
  owner: "text-violet-500 bg-violet-500/10 border-violet-500/30",
  customer: "text-pink-500 bg-pink-500/10 border-pink-500/30",
  date: "text-emerald-500 bg-emerald-500/10 border-emerald-500/30",
};

const STATE_TYPES = ["new", "open", "pending", "closed"] as const;

type FilterKey = "queue" | "owner" | "status" | "customer" | "from" | "to";

// Alias → canonical filter key. Both German and English spellings are accepted.
const KEY_ALIASES: Record<string, FilterKey> = {
  queue: "queue",
  besitzer: "owner",
  owner: "owner",
  status: "status",
  state: "status",
  kunde: "customer",
  customer: "customer",
  von: "from",
  from: "from",
  bis: "to",
  to: "to",
};

/** Parse ``key:fragment``; returns null when the text is not a recognised key. */
function parseKeyed(text: string): { key: FilterKey; frag: string } | null {
  const m = text.match(/^([\p{L}]+):(.*)$/u);
  if (!m) return null;
  const canonical = KEY_ALIASES[m[1].toLowerCase()];
  if (!canonical) return null;
  return { key: canonical, frag: m[2] };
}

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

type Suggestion = { id: string; kind: ChipKind | "text"; label: string; hint?: string; apply: () => void };

export function SmartSearchBar({
  values,
  queues,
  agents,
  onPatch,
  onSubmitQuery,
}: {
  values: SmartSearchValues;
  queues: QueueOption[];
  agents: AgentOption[];
  onPatch: (patch: Partial<{
    queue_id: number[];
    state_type: string[];
    owner_id?: number;
    customer_id?: string;
    customer_label?: string;
    created_from?: string;
    created_to?: string;
  }>) => void;
  onSubmitQuery: (term: string) => void;
}) {
  const { t } = useTranslation();
  // The input mirrors the active free-text query; while composing a key:token it
  // holds that transient text instead. It re-syncs whenever the URL query changes.
  const [text, setText] = useState(values.q);
  const [active, setActive] = useState(0);
  const [focused, setFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    setText(values.q);
  }, [values.q]);

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
        label: values.customerLabel || values.customerId,
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

  const commit = (patch: Parameters<typeof onPatch>[0]) => {
    onPatch(patch);
    // Drop the token text but keep the active free-text query visible.
    setText(values.q);
    setActive(0);
    inputRef.current?.focus();
  };

  // --- Build the suggestion list for the current input -------------------
  const suggestions: Suggestion[] = useMemo(() => {
    const raw = text.trim();
    if (!raw) return [];
    const parsed = parseKeyed(raw);

    if (parsed) {
      const frag = parsed.frag.toLowerCase().trim();
      if (parsed.key === "queue") {
        return queues
          .filter((qu) => qu.name.toLowerCase().includes(frag) && !values.queueIds.includes(qu.id))
          .slice(0, 6)
          .map((qu) => ({
            id: `queue-${qu.id}`,
            kind: "queue" as const,
            label: qu.name,
            apply: () => commit({ queue_id: [...values.queueIds, qu.id] }),
          }));
      }
      if (parsed.key === "owner") {
        return agents
          .filter(
            (a) =>
              a.full_name.toLowerCase().includes(frag) || a.login.toLowerCase().includes(frag),
          )
          .slice(0, 6)
          .map((a) => ({
            id: `owner-${a.id}`,
            kind: "owner" as const,
            label: a.full_name,
            hint: a.login,
            apply: () => commit({ owner_id: a.id }),
          }));
      }
      if (parsed.key === "status") {
        return STATE_TYPES.filter(
          (st) =>
            (st.includes(frag) || t(`search.filters.state.${st}`).toLowerCase().includes(frag)) &&
            !values.stateTypes.includes(st),
        ).map((st) => ({
          id: `status-${st}`,
          kind: "status" as const,
          label: t(`search.filters.state.${st}`),
          apply: () => commit({ state_type: [...values.stateTypes, st] }),
        }));
      }
      if (parsed.key === "customer") {
        const items: Suggestion[] = [];
        for (const c of customerQ.data?.companies ?? [])
          items.push({
            id: `co-${c.customer_id}`,
            kind: "customer",
            label: c.name,
            hint: c.customer_id,
            apply: () => commit({ customer_id: c.customer_id, customer_label: c.name }),
          });
        for (const c of customerQ.data?.contacts ?? []) {
          const name = `${c.first_name} ${c.last_name}`.trim() || c.login || c.customer_id;
          const label = c.company_name ? `${name} · ${c.company_name}` : name;
          items.push({
            id: `ct-${c.login}`,
            kind: "customer",
            label,
            hint: c.email,
            apply: () => commit({ customer_id: c.customer_id, customer_label: label }),
          });
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
            kind: "date",
            label: `${t(parsed.key === "from" ? "search.smart.from" : "search.smart.to")}: ${iso}`,
            apply: () => commit(patch),
          },
        ];
      }
      return [
        {
          id: "date-hint",
          kind: "date",
          label: t("search.smart.dateHint"),
          apply: () => {},
        },
      ];
    }

    // Not a recognised key yet: offer matching filter keys, plus full-text.
    const low = raw.toLowerCase();
    const keyHints: Suggestion[] = (["queue", "besitzer", "status", "kunde", "von", "bis"] as const)
      .filter((k) => k.startsWith(low))
      .map((k) => ({
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
    return [
      ...keyHints,
      {
        id: "fulltext",
        kind: "text",
        label: t("search.smart.fulltext", { text: raw }),
        hint: "Enter",
        apply: () => {
          onSubmitQuery(raw);
          setFocused(false);
        },
      },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, queues, agents, values, customerQ.data, t]);

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" && suggestions.length) {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp" && suggestions.length) {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const chosen = suggestions[active];
      if (chosen) chosen.apply();
      else {
        onSubmitQuery(text.trim());
        setFocused(false);
      }
    } else if (e.key === "Backspace" && text === "" && chips.length) {
      chips[chips.length - 1].remove();
    } else if (e.key === "Escape") {
      setText("");
    }
  };

  const showSuggest = focused && text.trim().length > 0 && suggestions.length > 0;

  return (
    <div className="flex gap-2">
      <div className="relative flex-1">
        <div
          className="flex min-h-[42px] flex-wrap items-center gap-1.5 rounded-md border border-hairline bg-surface px-2.5 py-1.5 focus-within:border-accent focus-within:outline focus-within:outline-2 focus-within:outline-offset-1 focus-within:outline-accent"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) inputRef.current?.focus();
          }}
          data-testid="smart-search-field"
        >
          <SearchIcon className="shrink-0 text-[16px] text-muted" />
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
            onChange={(e) => {
              setText(e.target.value);
              setActive(0);
            }}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            onKeyDown={onKeyDown}
            placeholder={chips.length ? "" : t("search.smart.placeholder")}
            data-testid="search-input"
            autoComplete="off"
            spellCheck={false}
            className="min-w-[8rem] flex-1 bg-transparent py-0.5 text-sm text-ink placeholder:text-muted focus:outline-none"
          />
        </div>

        {showSuggest && (
          <ul
            className="absolute z-20 mt-1 w-full overflow-hidden rounded-md border border-hairline bg-surface shadow-lg"
            data-testid="smart-search-suggest"
          >
            {suggestions.map((s, i) => (
              <li key={s.id}>
                <button
                  type="button"
                  onMouseDown={(e) => {
                    e.preventDefault();
                    s.apply();
                  }}
                  onMouseEnter={() => setActive(i)}
                  className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm ${
                    i === active ? "bg-surface-subtle" : ""
                  }`}
                  data-testid={`smart-suggest-${s.id}`}
                >
                  {s.kind !== "text" && (
                    <span
                      className={`h-2 w-2 shrink-0 rounded-full ${CHIP_CLASS[s.kind]}`}
                      aria-hidden
                    />
                  )}
                  <span className="min-w-0 flex-1 truncate text-ink">{s.label}</span>
                  {s.hint && <span className="shrink-0 text-xs text-muted">{s.hint}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <button
        type="button"
        onClick={() => onSubmitQuery(text.trim() || values.q)}
        className="shrink-0 rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-ink transition-colors duration-100 hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        data-testid="search-submit"
      >
        {t("search.submit")}
      </button>
    </div>
  );
}
