import { useEffect, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { StateChip } from "@/components/ui/StatusChip";
import { SearchIcon } from "@/components/ui/icons";
import { SmartSearchBar } from "@/components/agent/SmartSearchBar";
import {
  applySmartPatch,
  smartValuesToSearchParams,
  type SmartSearchValues,
} from "@/components/agent/smartSearch";
import { cn } from "@/lib/cn";

/**
 * Top-bar search. Click / ⌘K / Ctrl-K / "/" expands the field **inline** in the
 * header (no modal dialog). Same token-aware {@link SmartSearchBar} as the full
 * search page; live ticket hits appear in a dropdown under the field. Enter or
 * "show all results" navigates to /agent/search; picking a hit opens the ticket.
 */

const EMPTY: SmartSearchValues = { q: "", queueIds: [], stateTypes: [] };

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

export function CommandSearch({ fill = false }: { fill?: boolean }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<SmartSearchValues>(EMPTY);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setOpen(true);
        return;
      }
      // "/" opens the palette too (legacy shortcut), unless typing in a field.
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const el = e.target as HTMLElement | null;
        const tag = el?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) {
          return;
        }
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Reset the composed query/filters whenever the palette closes.
  useEffect(() => {
    if (!open) setValues(EMPTY);
  }, [open]);

  // Click outside closes the inline panel.
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      const root = rootRef.current;
      if (!root) return;
      if (e.target instanceof Node && !root.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [open]);

  const queuesQ = useQuery({
    queryKey: ["reference", "queues"],
    queryFn: ({ signal }) => api.listReferenceQueues({}, signal),
    enabled: open,
  });
  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: ({ signal }) => api.listReferenceAgents(signal),
    enabled: open,
  });

  const params = smartValuesToSearchParams(values);
  const paramsKey = JSON.stringify(params);
  const debouncedKey = useDebouncedValue(paramsKey, 220);
  const debouncedParams = JSON.parse(debouncedKey) as ReturnType<typeof smartValuesToSearchParams>;
  const hasQuery = typeof debouncedParams.q === "string" && debouncedParams.q.trim().length > 0;

  const resultsQ = useQuery({
    queryKey: ["command-search", debouncedKey],
    queryFn: ({ signal }) =>
      api.search(
        {
          q: debouncedParams.q ?? "",
          limit: 8,
          queue_id: debouncedParams.queue_id,
          state_type: debouncedParams.state_type,
          owner_id: debouncedParams.owner_id,
          customer_id: debouncedParams.customer_id,
          created_from: debouncedParams.created_from,
          created_to: debouncedParams.created_to,
        },
        signal,
      ),
    enabled: open && hasQuery,
  });

  const hasFilters =
    values.queueIds.length > 0 ||
    values.stateTypes.length > 0 ||
    values.ownerId != null ||
    Boolean(values.customerId) ||
    Boolean(values.createdFrom) ||
    Boolean(values.createdTo);

  const openFull = (term: string) => {
    const search = smartValuesToSearchParams({ ...values, q: term });
    if (!search.q && !hasFilters) return;
    setOpen(false);
    void navigate({ to: "/agent/search", search });
  };

  const openTicket = (id: number) => {
    setOpen(false);
    void navigate({ to: "/agent/tickets/$ticketId", params: { ticketId: String(id) } });
  };

  const hits = resultsQ.data?.hits ?? [];
  // Show the results panel once the user has typed free text (or while loading).
  const showResultsPanel = open && (hasQuery || Boolean(values.q.trim()));

  if (!open) {
    return (
      <button
        type="button"
        data-testid="command-search-trigger"
        onClick={() => setOpen(true)}
        className={
          fill
            ? "flex h-9 w-full items-center gap-2 rounded-lg border border-hairline bg-surface-subtle px-3 text-muted transition-colors duration-100 hover:border-accent/40 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
            : "flex h-8 items-center gap-2 rounded-lg border border-hairline bg-surface-subtle pl-2.5 pr-2 text-muted transition-colors duration-100 hover:border-accent/40 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        }
      >
        <SearchIcon className="text-[15px] shrink-0" />
        <span className={fill ? "text-[13px]" : "hidden text-[12.5px] sm:inline"}>
          {t("search.title")}
        </span>
        <kbd
          className={
            (fill ? "ml-auto " : "hidden sm:inline ") +
            "shrink-0 rounded border border-hairline bg-surface px-1.5 py-0.5 font-mono text-[10px] font-medium"
          }
        >
          ⌘K
        </kbd>
      </button>
    );
  }

  return (
    <div
      ref={rootRef}
      data-testid="command-search-form"
      className={cn("relative", fill ? "w-full" : "min-w-[16rem] sm:min-w-[22rem]")}
    >
      <SmartSearchBar
        values={values}
        queues={(queuesQ.data ?? []).map((qu) => ({ id: qu.id, name: qu.name }))}
        agents={(agentsQ.data ?? []).map((a) => ({
          id: a.id,
          full_name: a.full_name,
          login: a.login,
        }))}
        onPatch={(patch) => setValues((v) => applySmartPatch(v, patch))}
        onSubmitQuery={openFull}
        onQueryChange={(text) => setValues((v) => ({ ...v, q: text }))}
        onEscape={() => setOpen(false)}
        inputTestId="command-search-input"
        submitLabel={null}
        autoFocus
        compact
        freeTextSuggest={false}
      />

      {showResultsPanel && (
        <div
          className="absolute left-0 right-0 top-full z-50 mt-1 overflow-hidden rounded-lg border border-hairline bg-surface shadow-lg"
          data-testid="command-search-results"
        >
          <div className="max-h-[min(24rem,70vh)] overflow-y-auto p-1.5">
            {!hasQuery && (
              <p className="px-2 py-3 text-xs text-muted">{t("search.hint")}</p>
            )}
            {hasQuery && resultsQ.isLoading && (
              <div className="flex justify-center py-4">
                <Spinner className="h-5 w-5" />
              </div>
            )}
            {hasQuery && !resultsQ.isLoading && hits.length === 0 && (
              <p className="px-2 py-3 text-xs text-muted">{t("search.noResults")}</p>
            )}
            <ul className="space-y-0.5">
              {hits.map((hit) => (
                <li key={hit.id}>
                  <button
                    type="button"
                    onClick={() => openTicket(hit.id)}
                    data-testid={`command-search-hit-${hit.id}`}
                    className="flex w-full items-center gap-2 rounded-md border border-transparent px-2 py-1.5 text-left hover:border-hairline hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                  >
                    <span className="shrink-0 font-mono text-[11px] text-accent">{hit.tn}</span>
                    <span className="min-w-0 flex-1 truncate text-sm text-ink">{hit.title}</span>
                    {hit.state && <StateChip state={hit.state} />}
                    {hit.queue_name && (
                      <span className="hidden shrink-0 text-xs text-muted sm:inline">
                        {hit.queue_name}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="flex items-center justify-end border-t border-hairline px-2 py-1.5">
            <Button
              variant="secondary"
              size="sm"
              data-testid="command-search-viewall"
              onClick={() => openFull(values.q)}
            >
              {t("search.smart.viewAll")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
