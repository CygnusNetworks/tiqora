import { useQuery } from "@tanstack/react-query";
import { useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import { useDebounced } from "./useDebounced";

export type LookupHit = { value: string; label: string; hint?: string };

const INPUT_CLS =
  "w-full rounded border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60";

/**
 * Typeahead over a server lookup for the `ci_ref` / `company` field kinds. The field shows `shownLabel`
 * while nothing is typed; only picking a hit changes the value (free text is never a value), and the
 * clear button resets it. The search term is debounced and starts at two characters.
 */
export function LookupCombobox({
  id,
  testId,
  ariaLabel,
  hasValue,
  shownLabel,
  queryKey,
  search,
  onPick,
  onClear,
  disabled,
  invalid,
  required,
  describedBy,
  placeholder,
}: {
  id: string;
  testId: string;
  ariaLabel: string;
  hasValue: boolean;
  shownLabel: string;
  queryKey: readonly unknown[];
  search: (term: string) => Promise<LookupHit[]>;
  onPick: (hit: LookupHit) => void;
  onClear: () => void;
  disabled?: boolean;
  invalid?: boolean;
  required?: boolean;
  describedBy?: string;
  placeholder?: string;
}) {
  const { t } = useTranslation();
  const [typed, setTyped] = useState<string | null>(null);
  const [hi, setHi] = useState(0);
  const term = useDebounced((typed ?? "").trim(), 200);
  const open = typed !== null && term.length >= 2 && !disabled;
  const q = useQuery({
    queryKey: [...queryKey, term],
    queryFn: () => search(term),
    enabled: open,
  });
  const hits = q.data ?? [];
  const listId = `${id}-list`;

  function pick(hit: LookupHit) {
    onPick(hit);
    setTyped(null);
    setHi(0);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHi((i) => Math.min(i + 1, hits.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && hits[hi]) {
      e.preventDefault();
      pick(hits[hi]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setTyped(null);
    }
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-2">
        <input
          id={id}
          data-testid={testId}
          type="text"
          role="combobox"
          autoComplete="off"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-label={ariaLabel}
          aria-invalid={invalid || undefined}
          aria-required={required || undefined}
          aria-describedby={describedBy}
          value={typed ?? shownLabel}
          disabled={disabled}
          placeholder={placeholder}
          className={cn(INPUT_CLS, invalid && "border-danger focus:ring-danger")}
          onChange={(e) => {
            setTyped(e.target.value);
            setHi(0);
          }}
          onKeyDown={onKeyDown}
          onBlur={() => window.setTimeout(() => setTyped(null), 150)}
        />
        {hasValue && !disabled && (
          <button
            type="button"
            data-testid={`${testId}-clear`}
            onClick={onClear}
            className="shrink-0 rounded border border-hairline px-2 py-1 text-xs text-muted hover:text-ink focus:outline-none focus-visible:ring-1 focus-visible:ring-accent"
          >
            {t("cmdb.form.clear")}
          </button>
        )}
      </div>
      {open && (
        <ul
          id={listId}
          role="listbox"
          data-testid={`${testId}-results`}
          className="absolute left-0 right-0 z-20 mt-1 max-h-56 overflow-y-auto rounded border border-hairline bg-surface py-1 shadow-lg"
        >
          {q.isLoading ? (
            <li className="px-2 py-1.5 text-xs text-muted">…</li>
          ) : hits.length === 0 ? (
            <li className="px-2 py-1.5 text-xs text-muted">{t("cmdb.form.noHits")}</li>
          ) : (
            hits.map((h, i) => (
              <li key={h.value} role="option" aria-selected={i === hi}>
                <button
                  type="button"
                  tabIndex={-1}
                  data-testid={`${testId}-result-${h.value}`}
                  // mousedown beats the input's blur, which would close the list first
                  onMouseDown={(e) => {
                    e.preventDefault();
                    pick(h);
                  }}
                  onMouseEnter={() => setHi(i)}
                  className={cn(
                    "flex w-full items-baseline gap-2 px-2 py-1 text-left text-sm",
                    i === hi ? "bg-accent-dim" : "hover:bg-surface-subtle",
                  )}
                >
                  <span className="min-w-0 flex-1 truncate text-ink">{h.label}</span>
                  {h.hint && <span className="shrink-0 truncate text-xs text-muted">{h.hint}</span>}
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
