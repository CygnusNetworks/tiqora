import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { CustomerPickerDialog } from "@/components/customers/CustomerPickerDialog";
import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { FieldKindProps } from "./types";

const INPUT_CLS =
  "w-full rounded border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60";

function useDebounced<T>(value: T, ms: number): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const h = window.setTimeout(() => setOut(value), ms);
    return () => window.clearTimeout(h);
  }, [value, ms]);
  return out;
}

/**
 * Customer user of a process form: a text input with a search popover over `GET /reference/customers`
 * plus a button that opens the customer picker. The value is the customer user login; the server fills
 * a missing customer number from the picked customer user.
 */
export function CustomerKind({ def, value, onChange, id, testId, disabled, invalid, required, describedBy }: FieldKindProps) {
  const { t } = useTranslation();
  const login = typeof value === "string" ? value : "";
  // `typed` is what the user is searching for; it only drives the popover, the field shows `login`.
  const [typed, setTyped] = useState<string | null>(null);
  const [hi, setHi] = useState(0);
  const [pickerOpen, setPickerOpen] = useState(false);
  const locked = disabled || def.readOnly;
  const term = useDebounced((typed ?? "").trim(), 200);
  const open = typed !== null && term.length >= 2 && !locked;

  const searchQ = useQuery({
    queryKey: ["reference", "customers", term],
    queryFn: ({ signal }) => api.searchReferenceCustomers({ q: term }, signal),
    enabled: open,
  });
  const hits = searchQ.data ?? [];
  const listId = `${id}-list`;

  function pick(loginValue: string) {
    onChange(loginValue);
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
      pick(hits[hi].login);
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
          aria-label={def.label}
          aria-invalid={invalid || undefined}
          aria-required={required || undefined}
          aria-describedby={describedBy}
          value={typed ?? login}
          disabled={locked}
          placeholder={t("process.form.customerSearch")}
          className={cn(INPUT_CLS, invalid && "border-danger focus:ring-danger")}
          onChange={(e) => {
            setTyped(e.target.value);
            setHi(0);
            // A hand-typed login is a value too (Znuny takes the login as typed).
            onChange(e.target.value);
          }}
          onKeyDown={onKeyDown}
          onBlur={() => window.setTimeout(() => setTyped(null), 150)}
        />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="shrink-0"
          disabled={locked}
          data-testid={`${testId}-pick`}
          onClick={() => setPickerOpen(true)}
        >
          {t("process.form.customerPick")}
        </Button>
      </div>
      {open && (
        <ul
          id={listId}
          role="listbox"
          data-testid={`${testId}-results`}
          className="absolute left-0 right-0 z-20 mt-1 max-h-56 overflow-y-auto rounded border border-hairline bg-surface py-1 shadow-lg"
        >
          {searchQ.isLoading ? (
            <li className="px-2 py-1.5 text-xs text-muted">…</li>
          ) : hits.length === 0 ? (
            <li className="px-2 py-1.5 text-xs text-muted">{t("customerWorkbench.noHits")}</li>
          ) : (
            hits.map((c, i) => (
              <li key={c.login} role="option" aria-selected={i === hi}>
                <button
                  type="button"
                  tabIndex={-1}
                  data-testid={`${testId}-result-${c.login}`}
                  // mousedown beats the input's blur, which would close the list first
                  onMouseDown={(e) => {
                    e.preventDefault();
                    pick(c.login);
                  }}
                  onMouseEnter={() => setHi(i)}
                  className={cn(
                    "flex w-full items-baseline gap-2 px-2 py-1 text-left text-sm",
                    i === hi ? "bg-accent-dim" : "hover:bg-surface-subtle",
                  )}
                >
                  <span className="min-w-0 flex-1 truncate text-ink">{c.full_name || c.login}</span>
                  <span className="shrink-0 truncate text-xs text-muted">{c.email}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      )}
      {pickerOpen && (
        <CustomerPickerDialog
          ticketId={0}
          currentCustomerUserId={login || null}
          onPick={(c) => pick(c.login)}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}
